# M1 — İskelet ve Havuz

> Bu klasördeki dosyalar Türkçedir (bkz. `AGENTS.md`); deponun geri kalanı
> İngilizce. Kütüphane adları ve yerleşik terimler İngilizce bırakıldı —
> onları aratabilmen için.

## Tek cümlede

Artık bir İsviçre belediyesinin tüm anonim ve limited şirketlerini resmî
sicilden çekip kendi veritabanımıza yazabiliyor, ve bakmaya değmeyecekleri
kurallarla işaretleyebiliyoruz.

---

## Genel resim

### Sistem nereye gidiyor

Sekiz milestone var. Şu an birincisi bitti:

```
M1 ✅  havuz          bir beldenin tüm şirketleri → veritabanı, kurallarla eleme
M2 ⬜  puanlama       LLM her şirkete "hedefime uygun mu" diye puan verir
M3 ⬜  graph          LangGraph: 10'luk parti çek, her şirketi paralel işle
M4 ⬜  site bulma     web araması → şirketin sitesi hangisi
M5 ⬜  sayfa okuma    siteyi oku → yapılandırılmış profil çıkar
M6 ⬜  kişi + taslak  kime yazılacak → Almanca davet metni
M7 ⬜  review page    tek tek bak, Gönder/Atla (mail'i sen gönderirsin)
M8 ⬜  yayın          gizlilik notu, CI, halka açık sürüm
```

M1'in tek işi **huninin en geniş ağzı**: 5.467 şirketten kesinlikle işe
yaramayacakları ucuza ayıklamak, gerisini pahalı adımlara bırakmak.

```
5.467 şirket
   ↓  M1: kural  (bedava, 0,3 saniye)
4.335 şirket
   ↓  M2: LLM    (paralı, dakikalar)
 ~350 güçlü aday
```

### Şu an var olan parçalar

```
    sen ───► cli.py ───► settings.py        (.env'i okur, tek sefer)
                │
                ├──► load_pool.py ──┬──► lindas.py   (sicilden çeker)
                │                   └──► db.py       (veritabanına yazar)
                │
                └──► screen_pool.py ─┬──► screen.py  (kuralları uygular)
                                     └──► db.py      (etiketi yazar)

    her yerde ortak dil: models.py  →  CompanyRecord
    veritabanının şekli: schema.sql →  12 tablo (M1 bir tanesini doldurur)
```

Henüz olmayan: LangGraph, LLM, web araması, FastAPI, Docker.

---

## Verinin yolculuğu

Terminale yazdığın `3203` sayısının veritabanındaki satıra dönüşene kadarki
hâlleri. **Bu bölümü anlarsan gerisi ayrıntıdır.**

### `pool` komutu

```bash
uv run company-reach pool --municipality 3203 --run-id first
```

**1. `cli.py` — komut karşılanır**

typer, yazdığın metni fonksiyon çağrısına çevirir: `pool(municipality="3203",
run_id="first")`. Ayarlar okunur (`get_settings()`), veritabanı dosyası yoksa
oluşturulur (`init_db`).

**2. `lindas.py::build_query` — soru metne dönüşür**

`"3203"` bir SPARQL sorgusunun içine gömülür. Elimizde artık 873 karakterlik
düz bir metin var:

```sparql
admin:municipality <https://ld.admin.ch/municipality/3203> ;
FILTER(?lfIri IN (<...legalforms/0106>, <...legalforms/0107>))
ORDER BY ?uid LIMIT 2000 OFFSET 0
```

**3. `lindas.py::_post` — metin ağdan gider**

`https://lindas.admin.ch/query` adresine `POST` isteği. API anahtarı yok —
servis halka açık. Hata olursa 3 kez denenir.

**4. Cevap: JSON**

```json
{"uid":   {"type":"literal","value":"CHE100006546"},
 "name":  {"type":"literal","value":"..."},
 "desc":  {"type":"literal","value":"Betrieb einer ... Die Gesellschaft kann ..."}}
```

**5. `lindas.py::_record` — JSON nesneye dönüşür**

Burada üç şey aynı anda olur:

- Sarmalama açılır: `{"value": "CHE100006546"}` → `"CHE100006546"`
- `models.py` kimliği normalleştirir: noktalı/boşluklu ne gelirse gelsin
  `CHE` + 9 rakam olur
- `screen.py::head_clause` amaç metninin noter kuyruğunu keser ve sonucu
  `purpose_head` alanına koyar

Çıkan şey bir `CompanyRecord`. 2.000 tanesi bir listede.

**6. Sayfalama — 3 tur**

```
OFFSET 0     → 2000 kayıt   (tam dolu → devam)
OFFSET 2000  → 2000 kayıt   (tam dolu → devam)
OFFSET 4000  → 1467 kayıt   (eksik → son sayfa, dur)
```

**7. `db.py::upsert_companies` — liste diske yazılır**

5.467 kaydın her biri `companies` tablosunda bir satır olur. Aynı şirket zaten
varsa üzerine yazılır, ikizlenmez.

**8. Ekrana**

```
5467 companies stored for municipality 3203 (run first)
```

### `screen` komutu

```bash
uv run company-reach screen --run-id first
```

**1.** `screen_pool.py` o çalıştırmanın satırlarını veritabanından okur.

**2.** Her satır tekrar bir `CompanyRecord` olur. *(Baş cümle zaten adım 5'te
hesaplanmıştı — burada yeniden hesaplanmıyor.)*

**3.** `screen.py::screen_reason` karar verir:

```
isimde "in liquidation" var mı?                         → "in liquidation"
baş cümlede emlak var AND üretim sinyali YOK mu?        → "property only"
ikisi de değilse                                        → None  (kalır)
```

**4.** Karar aynı satırın `screen_reason` sütununa yazılır. **Hiçbir satır
silinmez** — sadece etiketlenir.

**5.** Ekrana: `kept 4335, dropped 1132`

### Sonuç: bir şirketin hâlleri

```
sicilde      →  "Erwerb, Halten und Verwaltung von Liegenschaften.
                 Die Gesellschaft kann Zweigniederlassungen errichten."
               ↓ head_clause
hafızada     →  purpose_head = "Erwerb, Halten und Verwaltung von Liegenschaften."
               ↓ screen_reason
veritabanında→  screen_reason = "property only"
               ↓ M2 bu sorguyu çalıştıracak
M2'ye giden  →  select * from companies where screen_reason is null   (dışarıda)
```

---

## Dosya dosya

| Dosya | Tek işi |
|---|---|
| `settings.py` | `.env` → tipli nesne |
| `models.py` | Bir şirket kaydı nedir |
| `screen.py` | Kim elenir (karar verir, yazmaz) |
| `schema.sql` | Veritabanının şekli |
| `tools/db.py` | Veritabanına nasıl yazılır |
| `tools/lindas.py` | Sicilden nasıl çekilir |
| `nodes/load_pool.py` | lindas + db'yi birleştirir |
| `nodes/screen_pool.py` | screen + db'yi birleştirir |
| `cli.py` | Komutları karşılar |

### `settings.py` — 47 satır

`.env` dosyasını okuyup her ayarı doğru tipe çevirir, eksik olan varsa
**programın ilk saniyesinde** hata verir.

**Kütüphane: pydantic-settings.** Alternatifi `os.environ` idi; onunla her
değer metin olarak gelir (`"10"`, `10` değil) ve eksik anahtar ancak o satıra
gelindiğinde patlar — yani beş bin şirket çekildikten sonra. pydantic-settings
ikisini de çözüyor, üstelik gizli değerleri `SecretStr` içine koyuyor:
`print(settings)` dediğinde anahtar değil `**********` görürsün.

İki ayrıntı: `db_path` saklanmıyor, `data_dir`'den **hesaplanıyor** — böylece
ikisi birbirini tutmamazlık edemez. `@lru_cache` ise `.env`'in bir çalıştırma
boyunca tek kez okunmasını sağlıyor; ayarı değiştirirsen komutu yeniden
çalıştırman gerekir.

### `models.py` — 34 satır

`CompanyRecord`: bir şirket kaydının dokuz alanı ve kuralları. Projedeki
**ortak dil** — `lindas.py` bunu üretir, `db.py` bunu yazar, `screen.py` bunu
okur.

**Kütüphane: pydantic.** Python'da `uid: str` yazmak normalde sadece bir
yorumdur, çalışma zamanında hiçbir şey yapmaz. pydantic onu **çalışan kurala**
çevirir: yanlış tip verirsen nesne hiç oluşmaz. Böylece "bu kayıt gerçekten
düzgün mü" kontrolü her fonksiyonun başında değil, tek bir yerde — verinin
sisteme girdiği anda — yapılır.

Kimlik normalleştirme (`models.py:25`) küçük ama kritik: sicil
`CHE100006546`, bir web sitesi `CHE-100.006.546` yazar. İkisi de içeri
girerken aynı biçime çevrilir, yoksa M4'te eşleştirme tutmaz.

### `screen.py` — 33 satır

İki kural, hiç ağ, hiç model. Girdi alır, cevap döndürür, **hiçbir şeyi
değiştirmez**.

`head_clause` amaç metninin noter kuyruğunu keser. Neden: metinlerin %96,7'si
`Die Gesellschaft kann ... Grundstücke erwerben ...` diye biter ve bu kalıp
her şirkette aynıdır. Kesmezsen bir marangozu emlak firması sanarsın.

`screen_reason` iki şey arar: tasfiye (isimde), ve "sadece emlak" (baş
cümlede). İkincisi çift koşullu: emlak kelimesi **var** ve üretim sinyali
**yok**. `Dienstleistung` bilerek üretim sinyali sayılmıyor — v0'da ölçüldü,
emlak firmaları da o kelimeyi kullanıyor (`LEARNINGS` §2).

Dosyanın ilkesi: **amaç metni eler, sınıflandırmaz.** "Bu kesinlikle işletme
değil" diyebilir, "bu kesinlikle marangoz" diyemez — çünkü metin şirketin
kuruluşta ne yapmayı planladığını anlatır, bugün ne yaptığını değil.

### `schema.sql` — 36 satır, 12 tablo

M1'de sadece `companies` doluyor; diğer 11'i M2–M7 için baştan tanımlı.
Sebebi: veri şekli sonradan parça parça büyümesin. Boş tablonun maliyeti yok,
"şimdi bunu nereye koyacağız" diye sonradan düşünmenin maliyeti var.

Neden ayrı tablolar, tek tabloya sütun eklemek yerine? Tek soru yetiyor:
**"bir şirket için bundan kaç tane olabilir?"** Bir tane ise sütun, birden
fazla ise tablo. Bir şirketin bir ismi var (sütun), ama birçok puanı olabilir
— hangi hedefe, hangi prompt sürümüne, hangi modele göre puanlandığına göre
değişir (tablo).

İkinci ayrım: `companies` **sicilin aynası** — bize ait değil, güncellenebilir.
`seen`, `scores`, `ledger` ise **bizim defterimiz** — yeniden hesaplanamaz,
kaybolursa kaybolur. Karıştırmıyoruz. `screen_reason`'ın `companies` içinde
bir sütun olması da bu kurala uyuyor: o sicilden hesaplanıyor, silinse tek
komutla geri gelir.

### `tools/db.py` — 85 satır

Üç iş: bağlantı aç, işlemi güvene al, kayıtları yaz.

**Kütüphane: sqlite3** (Python'un içinden geliyor, kurulum yok). Veritabanı
tek bir dosya: `data/company_reach.db`. Sunucu, şifre, port yok. Dosyayı
kopyalarsan veritabanını kopyalamış olursun. Alternatifi PostgreSQL idi; tek
kullanıcılı, tek makinede çalışan bir araç için kurulum yükü boşuna.

Neden dosyaya JSON yazmak yerine veritabanı? v0 öyle yapmıştı (`data/v0/`
klasörüne bakabilirsin). Küçükken çalışır, sonra çatırdar: "puanı 8'in üstünde
olup henüz bakmadıklarım" gibi bir soruyu dört dosyayı elle kesiştirerek
cevaplamak gerekir. Veritabanında bu tek satırlık bir sorgu.

**Kütüphane: yok — düz SQL.** ORM (SQLAlchemy gibi) kullanmadık. Tasarımın
kuralı: bir adım açıkça gerektirmeden çatı ekleme. On iki tablo ve birkaç
sorgu için SQL'i doğrudan yazmak hem daha az kod, hem okurken ne olduğu belli.

### `tools/lindas.py` — 116 satır

Sicilden çeker, `CompanyRecord` listesi döndürür. **Veritabanını hiç
bilmez** — içinde tek bir `insert` yoktur.

**Kütüphane: httpx.** Alternatifi `requests` idi; httpx'i seçtik çünkü timeout
ayarı açık (bağlanma ve okuma ayrı) ve test tarafında `respx` ile temiz
taklit ediliyor.

İçinde dört fonksiyon var ama sen sadece `fetch_companies`'i çağırıyorsun.
Diğer üçü içeriye ait. *(Konvansiyon: başında alt çizgi olan isimler —
`_post`, `_record` — "bunu dışarıdan çağırma" demektir. `build_query`'de alt
çizgi yok çünkü onu test çağırıyor.)*

En önemli tasarım kararı `lindas.py:68` ve `112`: hata olduğunda **boş liste
değil, hata fırlatılıyor**. Aksi hâlde "bu belediyede şirket yok" ile "LINDAS
çökmüş" aynı görünürdü ve havuz sessizce boş yazılırdı.

### `nodes/load_pool.py` ve `nodes/screen_pool.py`

Birleştirme katmanı. `load_pool` üç satır: çek, bağlan, yaz.

Burası aynı zamanda **`Settings`'i parçalayıp dağıtan** tek yer —
`settings.lindas_url` aşağıya `url` olarak gider. `lindas.py` `Settings`
diye bir şeyin varlığını bilmez; testlerinin onu sahte bir adrese
yönlendirebilmesinin sebebi bu.

*(Konvansiyon: klasörün adı `nodes/` ama içinde sıradan fonksiyonlar var.
Sebep M3 — bunlar LangGraph'ta birer düğüm olacak. Bir klasör adı kodun
bugünkü hâlinden çok gideceği yeri anlatıyorsa, mimarinin planlı olduğunun
işaretidir.)*

### `cli.py` — 39 satır

**Kütüphane: typer.** Fonksiyon imzasını komuta çeviriyor: `--help` metni
docstring'den, seçenek isimleri parametre adlarından geliyor. Alternatifi
`argparse` idi (standart kütüphane); typer'ı seçtik çünkü tip ipuçlarını
zaten yazdığımız için ekstra tanım gerektirmiyor.

*(Konvansiyon: typer'da varsayılanı olan parametre seçenek olur
(`--municipality`), olmayan konumsal argüman olur. `screen` komutunda
`Annotated[str, typer.Option(...)]` yazmamızın sebebi bu — varsayılansız
ama yine de seçenek olsun istedik.)*

---

## Yeni kavramlar

Her biri: **hangi derdi çözüyor → bizim kodda nerede → genel adı ne.**
Genel adı önemli, çünkü aratabileceğin şey o.

### 1. Transaction

**Dert:** 5.467 şirket yazarken 3.000'incide program çökerse, yarım havuz
kalır. Yarım veri, hiç veriden kötüdür — çünkü doğru sanırsın.

**Kodda:** `tools/db.py:45-48`. `with conn:` bloğunun içindeki tüm yazmalar ya
hep birlikte kalıcı olur (**commit**), ya hata çıkarsa hepsi geri alınır
(**rollback**).

**Genel adı:** *transaction*. Klasik örneği banka havalesi: hesaptan düş +
diğerine ekle, ikisi birlikte olmalı.

**Tuzak:** `with conn:` commit eder ama bağlantıyı **kapatmaz**. Kapatmayı
`finally` yapıyor. İnsanların en sık yanıldığı yer; `tests/test_db.py` tam
bunu doğruluyor.

### 2. PRAGMA ve WAL

**PRAGMA nedir:** SQLite'a verilen ayar komutu. Normal SQL değil — tabloyla
ilgilenmiyor, veritabanının kendi davranışını ayarlıyor. Üç tane
kullanıyoruz:

| PRAGMA | Ne yapar |
|---|---|
| `journal_mode=WAL` | Biri yazarken başkası **okuyabilsin** |
| `busy_timeout=5000` | Meşgulse hemen hata verme, 5 saniye bekle |
| `foreign_keys=ON` | Tablolar arası bağları denetle |

**WAL** = *Write-Ahead Logging*. Normalde SQLite yazarken tüm dosyayı kilitler
ve okuyanlar bekler. WAL'de yazmalar önce ayrı bir kayda gider, okuyanlar eski
hâli görmeye devam eder. M7'de review page açıkken arka planda çalıştırma
sürecek — bu yüzden gerekli.

**Ve bir sıra kuralı:** PRAGMA'lar bir transaction'ın **içinde** çalışamaz.
`autocommit=False` ise ilk komuttan önce bir transaction açar. Yani:

```
YANLIŞ:  transaction aç → PRAGMA ver     →  "cannot change into wal mode
                                             from within a transaction"
DOĞRU:   PRAGMA ver → transaction kontrolünü devral
```

`tools/db.py:22-27` bu sırayı uyguluyor. Planda ters yazılmıştı; hatayı test
yakaladı.

### 3. `?` ve SQL injection

**Dert:** Bir değeri SQL metninin içine yapıştırırsan, o değerin içindeki
karakterler komutun **yapısını** değiştirebilir. Şirket adı `Muster'); DELETE
FROM companies; --` olsaydı tablon silinirdi.

**Kodda:** `tools/db.py:77`, `nodes/screen_pool.py`. Değerler metne
yazılmıyor; yerlerine `?` konuyor ve gerçek değerler ayrıca veriliyor.
Veritabanı önce cümlenin yapısını sabitliyor, sonra değerleri **veri olarak**
yerleştiriyor.

**Genel adı:** *parameterised query* / *prepared statement*. Karşıtı: *SQL
injection*, yazılım güvenliğinin en eski ve en yaygın açığı.

**Kural:** istisnasız her yerde `?`. "Tehlikeli yerde uygularım" diye
öğrenirsen bir gün unutursun.

### 4. Idempotency

**Dert:** `pool` komutunu ikinci kez çalıştırdın. İkiz satırlar mı oluşacak?

**Kodda:** `tools/db.py:78` — `ON CONFLICT(uid) DO UPDATE`. "Bu kimlik zaten
varsa hata verme, üzerine yaz."

**Genel adı:** *idempotency* — aynı işlemi bir kez de yapsan beş kez de
yapsan sonuç aynı. Otomatik sistemlerde altın kural, çünkü bir işlemin
gerçekten tamamlanıp tamamlanmadığını her zaman bilemezsin (ağ koptu, süreç
öldü, kullanıcı iki kez tıkladı).

### 5. Mock ve fixture

**Dert:** `lindas.py`'ı test etmek için her seferinde gerçek LINDAS'a gitmek
zorunda mıyız? Olmaz: yavaş, internet gerektirir, ve sicil değişince test
kırılır — oysa bizim kodumuzda sorun yok.

**Kodda:** `tests/test_lindas.py`. `@respx.mock` decorator'ü testin süresince
tüm ağ trafiğini yakalıyor; `tests/fixtures/lindas_page.json` ise LINDAS'ın
vereceği cevabın uydurma bir örneği.

**Genel adları:** *mock* (gerçeğin yerine geçen sahte), *fixture* (testin
kullandığı hazır veri). `conftest.py` ise pytest'in özel dosyası: oradaki
`settings` fikstürü, adını parametre olarak yazan her teste otomatik gider.

### 6. Dependency injection

**Dert:** `fetch_companies` LINDAS adresini nereden bilsin? İçeriden
`get_settings()` çağırsa çalışırdı — ama o zaman testte sahte bir adrese
yönlendiremezdik.

**Kodda:** `tools/lindas.py:89` — adres `url` parametresi olarak **dışarıdan
veriliyor**. Kim veriyor? `nodes/load_pool.py`.

**Genel adı:** *dependency injection*. Tek cümlesi: **bir fonksiyon ihtiyacı
olan şeyi kendisi gidip almasın, kendisine verilsin.** Faydası imzada görünür
olması — fonksiyonun neye bağımlı olduğunu içini okumadan anlarsın.

### 7. Saf fonksiyon ve yan etki

**Dert:** Bir dosyayı açtığında "bu kod dünyayı değiştiriyor mu, yoksa sadece
hesap mı yapıyor?" sorusunu hızlı cevaplamak.

**Kodda:**

| | `screen.py::screen_reason` | `nodes/screen_pool.py` |
|---|---|---|
| Ne yapar | Hesaplar, cevap döndürür | Veritabanını değiştirir |
| İki kez çağırsan | Hiçbir şey olmaz | Tekrar yazar |

**Genel adları:** *pure function* (saf) ve *side effect* (yan etki).

**Kod okuma tekniği:** tanımadığın bir dosyada şu kelimeleri ara —
`execute · insert · update · delete · write · save · commit · post · send`.
Bunlar dünyayı değiştiren satırlardır. Hiç yoksa dosya sadece hesap
yapıyordur. `lindas.py`'da hiç `insert` olmaması, onun veritabanını
doldurmadığını tek bakışta söylüyor.

### 8. Test önce yazma sırası

Her görevde aynı ritim vardı:

```
testi yaz → çalıştır, BAŞARISIZ olduğunu gör → kodu yaz → geçir → lint → commit
```

"Başarısız olduğunu gör" adımı atlanmadı. Sebebi: hiç başarısız olmamış bir
test, hiçbir şey ölçmüyor olabilir — yanlış dosyaya bakıyordur, yanlış şeyi
kontrol ediyordur.

**Genel adı:** *test-driven development* (TDD). Bu projede iki gerçek hatayı
yakaladı (aşağıda).

---

## Kendin dene

```bash
cd ~/Developer/alignor/tools/company-reach     # proje kökü şart, aşağısı değil
cp .env.example .env                           # M1 anahtarı kullanmıyor, dosya yeterli

uv run pytest -q                               # 18 passed, ağa çıkmadan
uv run company-reach --help
uv run company-reach pool --municipality 3203 --run-id first
uv run company-reach screen --run-id first
```

Beklenen: `5467 companies stored` · `kept 4335, dropped 1132`.

Sonra veriye bak:

```bash
sqlite3 data/company_reach.db \
  "select coalesce(screen_reason,'(kept)'), count(*) from companies group by 1;" \
  -header -column
```

`(kept) 4335 · property only 799 · in liquidation 333`

Bir şirketin tüm alanlarını gör:

```bash
sqlite3 data/company_reach.db "select * from companies limit 1;" -line
```

### Bilerek bozmayı dene

Anlamanın en hızlı yolu bir şeyi kırıp ne olduğunu görmek:

1. **`pool`'u ikinci kez çalıştır.** Satır sayısı değişiyor mu?
   `select count(*) from companies;` → hâlâ 5.467. Neden: `ON CONFLICT`.

2. **`src/` klasörüne girip komutu çalıştır.** Program başlamıyor:
   `.env` bulunamıyor. Neden: `.env` ve `data/` göreli yollar, bulunduğun
   klasöre göre çözümleniyor. Bu yüzden **her zaman proje kökünde ol**.

3. **`.env` dosyasını sil, `pool`'u çalıştır.** İlk saniyede
   `llm_api_key Field required` hatası alırsın — beş bin şirket çekildikten
   sonra değil. pydantic-settings'in getirdiği fayda tam olarak bu.

4. **`screen.py`'da `_OPERATING` listesine `Dienstleistung` ekle**, `screen`'i
   tekrar çalıştır. `property only` sayısı **799'dan 519'a** düşer: 280 emlak
   şirketi "hizmet veriyorum" dediği için eleme ağından kaçar. v0'da ölçülen
   şey buydu, o kelimenin listede olmamasının sebebi de bu.

---

## Kendini sına

Cevapları bu sayfada. Önce hatırlamaya çalış, sonra bak.

1. `screen_pool` hangi şirketleri siliyor?
2. Kalan şirketler nerede tutuluyor?
3. `PRAGMA journal_mode=WAL` neden bağlantı açılırken, en başta çalıştırılmak
   zorunda?
4. `lindas.py` LINDAS çöktüğünde neden boş liste döndürmüyor?
5. Amaç metninin son cümlesini kesmezsek ne yanlış olur?
6. `fetch_companies`'e LINDAS adresini neden dışarıdan veriyoruz — ayarlardan
   kendisi okusa ne kaybederdik?
7. Bir bilgi için `companies` tablosuna sütun mu ekleyeceğine, yoksa yeni
   tablo mu açacağına nasıl karar verirsin?
8. `?` işaretleri yerine değerleri doğrudan SQL metnine yazsak ne olurdu?

---

## Şimdilik gerek yok

Bu milestone'da bilerek atlananlar — hangi milestone'da geleceğiyle birlikte:

| Konu | Ne zaman |
|---|---|
| LangGraph, `StateGraph`, `Send`, paralel çalıştırma | M3 |
| LLM çağrısı, structured output, prompt dosyaları | M2 |
| `async`/`await` ve eşzamanlılık | M3 |
| Docker ve `compose.yaml` | M4 (SearXNG ile) |
| FastAPI, HTML şablonları | M7 |
| `schema.sql`'deki diğer 11 tablo | M2–M7 |

`schema.sql`'i açıp `drafts` veya `ledger` tablosunu görünce "bunu anlamadım"
diye endişelenme — şu an kimse kullanmıyor.

---

## Açık uçlar

- **`.env` zorunluluğu.** `pool` hiç model çağrısı yapmadığı hâlde `Settings`
  `llm_api_key` ve `llm_model` alanlarını zorunlu tutuyor. 2026-09-20'de
  olduğu gibi bırakılmasına karar verildi; M2'de `doctor` komutuyla tekrar
  bakılacak.

- **Göreli yollar.** `.env` ve `data/` bulunduğun klasöre göre çözümleniyor,
  proje köküne göre değil. Alt klasörden çalıştırmak sessizce yanlış yere
  veritabanı yazabilir. Program şu an uyarmıyor.

- **Eleme v0'dan geçirgen.** v0'da 5.462 şirketin 3.993'ü kalmıştı; şimdi
  5.467'nin 4.335'i kalıyor. Fazla tutmak ucuz hata (gerisini M2 puanlıyor),
  ama v0'ın tam kural setiyle karşılaştırmaya değer.

- **Araştırma notuyla uyuşmazlık.** Not, `schema:description` alanının
  kayıtların %3,2'sinde eksik olduğunu söylüyor; 3203 numaralı belediye
  2026-09-20'de %100 dolu döndü. Kod yine de alanı isteğe bağlı sayıyor ve
  fixture'da metinsiz bir şirket duruyor.

- **Stale diyagram.** `docs/design/diagrams/` altındaki phase-2 diyagramı
  güncel değil (denetimde not edilmişti); bu milestone'da dokunulmadı.

### Planda bulunan iki hata

İkisini de test yakaladı — TDD'nin bu projede karşılığını verdiği yer:

1. **`autocommit=False` + `PRAGMA ... WAL` çalışmıyordu.** Sıra değişti: önce
   PRAGMA'lar, sonra transaction kontrolü (`tools/db.py:22-27`).

2. **Amaç metni olmayan şirket "property only" olarak elenemez.** Kurallar
   isme değil metne bakıyor; fixture'daki metinsiz şirket bu yüzden kalıyor.
   Eleme yolunu denemek için fixture'a üçüncü bir uydurma şirket eklendi.
   Tasarım ilkesiyle de uyumlu: *bilmiyorsak eleme.*

Bir de `ruff` 0.16'nın Markdown içindeki Python bloklarını biçimlendirip
tasarım dokümanlarını değiştirmesi — `pyproject.toml`'a
`extend-exclude = ["*.md"]` eklendi.

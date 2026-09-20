# M1 — İskelet ve Havuz

Ne yapıldı, her kütüphane neden orada, ve M2'ye geçmeden önce anlaşılmaya
değer beş şey. Plan: `docs/plans/2026-09-19-m1-skeleton-and-pool.md`.

> Bu klasördeki dosyalar Türkçedir (bkz. `AGENTS.md`); deponun geri kalanı
> İngilizce. Kütüphane adları ve yerleşik terimler İngilizce bırakıldı.

## Şu an ne çalışıyor

İki komut, gerçek sicile karşı uçtan uca:

```bash
uv run company-reach pool --municipality 3203 --run-id first
# → 5467 companies stored for municipality 3203 (run first)

uv run company-reach screen --run-id first
# → kept 4335, dropped 1132
```

2026-09-20'de 3203 numaralı belediyede (St. Gallen) ölçüldü: 5.467 şirket,
kimliklerin tamamı benzersiz, %96,7'si standart amaç kuyruğu taşıyor. 18 test,
hepsi ağsız çalışıyor. LangGraph yok, LLM yok, web araması yok — onlar M2 ve
sonrasında.

## Kullanılan kütüphaneler ve nedenleri

- **uv** — proje, sanal ortam, lockfile ve Python 3.13'ün kendisi; hepsi tek
  araçta. pip + venv + pip-tools üçlüsü yerine seçildi, çünkü üçünün yerini
  alıyor ve `uv.lock` temiz bir clone'da tam olarak aynı bağımlılık ağacını
  kuruyor.
- **pydantic** — `CompanyRecord` nesne oluşurken doğrulanıyor, yani var olan
  her kayıt doğru biçimde. Aynı sınıf M2'de LLM çıktı şeması olacak: tek
  tanım, iki iş.
- **pydantic-settings** — `.env` dosyasını tek bir tipli nesneye çeviriyor.
  `os.environ` yerine seçildi, çünkü tipleri dönüştürüyor, hatayı çalıştırmanın
  ortasında değil başında veriyor, ve gizli değerleri `SecretStr` içinde
  tutuyor (`repr()`'de ve loglarda görünmüyor).
- **httpx** — SPARQL POST isteği. `requests` yerine seçildi: timeout nesnesi
  açık, ve `respx` onu testlerde temiz biçimde taklit ediyor.
- **sqlite3** (standart kütüphane) — tek dosya, sunucu yok. WAL sayesinde M7
  review page'i bir çalıştırma sürerken okuma yapabilecek.
- **typer** — fonksiyon imzasını `--help` destekli bir komuta çeviriyor.
  Varsayılanı olan parametre seçenek (option), olmayan konumsal argüman olur.
- **respx** + **pytest** — deterministik testlerin tamamı ağsız çalışıyor.
- **ruff** — lint ve format. `pyproject.toml`'daki `extend-exclude = ["*.md"]`
  satırına dikkat: ruff 0.16 Markdown içindeki Python bloklarını da
  biçimlendiriyor ve ilk çalıştırmada tasarım dokümanlarını değiştirdi.

## Anlaşılmaya değer beş şey

1. **Önce PRAGMA, sonra transaction kontrolü** — `tools/db.py:22-27`.
   `autocommit=False`, ilk komuttan önce bir transaction açıyor;
   `PRAGMA journal_mode=WAL` ise bir transaction'ın içinde çalışamıyor. Bu
   yüzden bağlantı önce autocommit modunda açılıyor, üç PRAGMA çalışıyor, ve
   ancak ondan sonra `conn.autocommit = False` kontrolü devralıyor. Planda
   sıra tersti; hatayı test yakaladı.

2. **`with conn:` commit eder, kapatmaz** — `tools/db.py:45-48`. Başarılıysa
   commit, hata varsa rollback; ikisi de bağlantıyı kapatmıyor — kapatmayı
   `finally` yapıyor. Tek bir `with` bloğundaki tüm yazmalar ya hep ya hiç.

3. **Bulgu ile hata aynı şey değildir** — `tools/lindas.py:68` ve `112`. Üç
   deneme, sonra `LindasError`; sıfır şirket de hata fırlatıyor. İkisi de boş
   liste döndürseydi "bu belediyede şirket yok" ile "LINDAS çökmüş" ayırt
   edilemezdi ve havuz sessizce boş yazılırdı. Aynı ayrım M4 için de bir P1
   denetim şartı.

4. **Eleme etiketler, asla silmez** — `screen.py:26-33` karar veriyor,
   `nodes/screen_pool.py` `screen_reason` değerini mevcut satırın üstüne
   yazıyor. Kalanlar hiçbir yerde liste değil: `screen_reason IS NULL` olan
   satırlar. Her çalıştırmada tüm satırlar yeniden yazılıyor, böylece kural
   değişip komut tekrar çalıştırıldığında eski etiketler temizleniyor.

5. **Anlam head clause'da** — `screen.py:9,21-23`. Amaç metinlerinin %96,7'si
   noterin standart kuyruğuyla bitiyor ve o kuyrukta `Grundstücke` geçiyor.
   Kuralları çalıştırmadan önce kuyruğu kesmek, bir marangozun emlak firması
   sanılmasını engelleyen şey. `Dienstleistung` ve `Entwicklung` bilerek
   `_OPERATING` listesinde değil (`screen.py:13`): emlak firmaları da bu iki
   kelimeyi kullanıyor (LEARNINGS §2).

## Nereye bakmalı — okuma sırası

```
settings.py          (47 satır)  neler ayarlanabilir
models.py            (34 satır)  bir şirket kaydı nedir
screen.py            (33 satır)  iki kural
schema.sql           (36 satır)  12 tablo; M1 bir tanesini dolduruyor
tools/db.py          (85 satır)  bağlantı · transaction · upsert
tools/lindas.py     (116 satır)  build_query · _post · _record · fetch_companies
nodes/load_pool.py   (11 satır)  lindas + db, üç satır
nodes/screen_pool.py (28 satır)  oku · karar ver · etiketle
cli.py               (39 satır)  iki komut
```

`load_pool.py`, `Settings`'i parçalayıp her katmana yalnızca ihtiyacı olanı
veren tek yer — `lindas.py` `Settings`'i hiç import etmiyor. Testlerinin onu
sahte bir URL'e yönlendirebilmesinin sebebi bu.

## Şunları kontrol et

```bash
cp .env.example .env                       # M1'de anahtar kullanılmıyor, dosya yeterli
uv run pytest -q                           # 18 passed, ağsız
uv run company-reach --help
uv run company-reach pool --municipality 3203 --run-id first
uv run company-reach screen --run-id first
```

Sonra veriye bak:

```bash
sqlite3 data/company_reach.db \
  "select coalesce(screen_reason,'(kept)'), count(*) from companies group by 1;" \
  -header -column
```

Beklenen: yaklaşık `(kept) 4335 · property only 799 · in liquidation 333`.
`pool`'u ikinci kez çalıştır: satır sayısı 5.467'de kalıyor —
`ON CONFLICT(uid) DO UPDATE` (`tools/db.py:78`) tekrar çalıştırmayı güvenli
kılıyor.

Bir kez okumaya değer: `tests/test_lindas.py` — `respx`'in SPARQL POST
isteğine fixture'dan nasıl cevap verdiğini, yani testin ağa hiç çıkmadığını
görürsün.

## Açık uçlar

- `Settings`, `llm_api_key` ve `llm_model` alanlarını zorunlu tutuyor; bu
  yüzden `pool` hiç model çağrısı yapmadığı hâlde bir `.env` istiyor.
  2026-09-20'de olduğu gibi bırakılmasına karar verildi; M2'de `doctor` ile
  tekrar bakılacak.
- v0'da 5.462 şirketin 3.993'ü kalmıştı; aynı belediyede şimdi 5.467'nin
  4.335'i kalıyor. Buradaki iki kural v0'ınkinden daha geçirgen. Kabul
  edilebilir — fazla tutmak ucuz hata ve gerisini M2 puanlıyor — ama v0'ın
  tam kural setiyle karşılaştırmaya değer.
- Araştırma notu `schema:description` alanının kayıtların %3,2'sinde eksik
  olduğunu söylüyor; 3203 numaralı belediye 2026-09-20'de %100 dolu döndü.
  `_record` yine de alanı isteğe bağlı sayıyor ve fixture'da metinsiz bir
  şirket duruyor.
- `docs/design/diagrams/` altındaki phase-2 diyagramı güncel değil (denetimde
  not edilmişti); bu milestone'da dokunulmadı.

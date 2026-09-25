// Conveniences only: every action is a form POST and every card a URL, so
// the page works without this file (ux spec section 9).
(function () {
  var prev = document.getElementById("prev");
  var next = document.getElementById("next");
  var send = document.getElementById("send");
  var toast = document.getElementById("toast");

  // Never again and Bounced ask here, in the browser; without this script
  // the server asks on a page of its own instead (confirm=yes skips it).
  function confirmFirst(button, question) {
    if (!button) return;
    button.addEventListener("click", function (e) {
      if (!confirm(question)) {
        e.preventDefault();
        return;
      }
      var yes = document.createElement("input");
      yes.type = "hidden";
      yes.name = "confirm";
      yes.value = "yes";
      button.form.appendChild(yes);
    });
  }
  confirmFirst(document.getElementById("never"),
    "Never contact this company again? This cannot be undone.");
  confirmFirst(document.getElementById("bounced"),
    "Did the mail come back? Its address goes on the never-again list, and the card opens again for another address.");

  document.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
      if (t.type !== "radio") return; // keys stay with a field that has focus
    }
    if (e.key === "ArrowRight" && next && !next.classList.contains("off")) {
      location.href = next.href;
    } else if (e.key === "ArrowLeft" && prev && !prev.classList.contains("off")) {
      location.href = prev.href;
    } else if ((e.key === "s" || e.key === "S") && send && !send.disabled) {
      send.click();
    }
  });

  if (toast && toast.textContent.trim()) {
    setTimeout(function () { toast.classList.remove("show"); }, 1800);
    // keep the URL clean, so a reload does not show the toast again
    history.replaceState(null, "", location.pathname);
  }
})();

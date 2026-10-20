(function () {
  try {
    var t = localStorage.getItem("muse-theme");
    if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
  } catch (e) {}
})();

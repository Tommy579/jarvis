// Applique la preference d'apparence avant le premier rendu (evite le flash).
try {
  var s = localStorage.getItem("jarvis_color_scheme");
  if (s === "light" || s === "dark") {
    var meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.setAttribute("content", s);
  }
  if (localStorage.getItem("jarvis_accent") === "amber") {
    document.documentElement.setAttribute("data-theme", "amber");
  }
} catch (e) {}

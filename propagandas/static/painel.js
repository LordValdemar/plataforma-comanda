// Pede confirmação antes de enviar formulários marcados com data-confirmar.
document.querySelectorAll("form[data-confirmar]").forEach(function (form) {
  form.addEventListener("submit", function (evento) {
    if (!confirm(form.dataset.confirmar)) evento.preventDefault();
  });
});

// Barras de uso: a largura vem do atributo (estilo embutido no HTML é bloqueado pela CSP).
document.querySelectorAll("[data-largura]").forEach(function (barra) {
  barra.style.width = Math.max(0, Math.min(100, Number(barra.dataset.largura) || 0)) + "%";
});

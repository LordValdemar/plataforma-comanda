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

// Partes marcadas com data-atualizar (ex.: status das telas) se atualizam sozinhas a cada
// 15 s, sem recarregar a página (formulários abertos continuam como estão).
(function () {
  const regioes = document.querySelectorAll("[data-atualizar][id]");
  if (!regioes.length) return;
  async function atualizar() {
    if (document.hidden) return;
    try {
      const resposta = await fetch(location.href, { headers: { "X-Atualizacao": "1" }, cache: "no-store" });
      if (!resposta.ok || resposta.redirected) return;
      const novo = new DOMParser().parseFromString(await resposta.text(), "text/html");
      regioes.forEach(function (regiao) {
        const substituta = novo.getElementById(regiao.id);
        if (substituta && regiao.innerHTML !== substituta.innerHTML) regiao.innerHTML = substituta.innerHTML;
      });
    } catch (erro) { /* sem rede: tenta na próxima */ }
  }
  setInterval(atualizar, 15000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) atualizar(); });
})();

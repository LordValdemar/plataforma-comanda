// Tela do QR code do ponto: troca o código sozinha e mostra o relógio.
"use strict";

(function () {
  const api = document.body.dataset.api;
  const qr = document.getElementById("qr");
  const aviso = document.getElementById("aviso");
  const relogio = document.getElementById("relogio");
  let espera = null;

  async function atualizar() {
    clearTimeout(espera);
    try {
      const resposta = await fetch(api, { cache: "no-store" });
      if (!resposta.ok) throw new Error("HTTP " + resposta.status);
      const dados = await resposta.json();
      if (!dados.ativo) {
        qr.removeAttribute("src");
        aviso.textContent = "O controle de ponto está desligado.";
        aviso.className = "aviso erro";
        espera = setTimeout(atualizar, 30000);
        return;
      }
      qr.src = dados.qr;
      aviso.textContent = "O código muda a cada poucos segundos.";
      aviso.className = "aviso";
      espera = setTimeout(atualizar, Math.max(1, dados.troca_em) * 1000 + 300);
    } catch (erro) {
      aviso.textContent = "Sem conexão com o servidor. Tentando de novo…";
      aviso.className = "aviso erro";
      espera = setTimeout(atualizar, 10000);
    }
  }

  function tique() {
    relogio.textContent = new Date().toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  setInterval(tique, 1000);
  tique();
  atualizar();
  // Tela ligada o dia todo: recarrega a página uma vez por dia para receber atualizações.
  setTimeout(function () { location.reload(); }, 24 * 60 * 60 * 1000);
})();

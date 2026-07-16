// DMR EP — wpisywanie kluczy RC4 z panelu WWW OpenWebRX+.
// Author: SP8MB
// Wysyła klucze do backendu przez WebSocket (globalny `ws`), który zapisuje je
// w /opt/openwebrx-tetra/dmr_ep.key; dekoder DMR EP hot-reloaduje się z nowym kluczem.
var DmrEpKeys = {
    _status: function (text, color) {
        var s = document.getElementById('dmr-ep-key-status');
        if (s) { s.textContent = text; s.style.color = color || ''; }
    },

    // walidacja: dozwolone linie hex 2..64 znaków; komentarze '#' pomijane
    _clean: function (raw) {
        var out = [];
        (raw || '').split('\n').forEach(function (ln) {
            var c = ln.split('#')[0].trim();
            if (c && /^[0-9A-Fa-f]{2,64}$/.test(c)) out.push(c.toUpperCase());
        });
        return out;
    },

    save: function () {
        var el = document.getElementById('dmr-ep-key-input');
        var raw = el ? el.value : '';
        var keys = DmrEpKeys._clean(raw);
        if (!keys.length) {
            DmrEpKeys._status('Podaj klucz hex (np. A1B2C3D4E5)', '#ff6060');
            return;
        }
        if (typeof ws === 'undefined' || !ws || ws.readyState !== 1) {
            DmrEpKeys._status('Brak połączenia z odbiornikiem', '#ff6060');
            return;
        }
        ws.send(JSON.stringify({ type: 'dmr_ep_key', keys: keys.join('\n') }));
        DmrEpKeys._status('Zapisano ' + keys.length + ' klucz(y) — przełącz tryb, by zastosować', '#4caf50');
    }
};

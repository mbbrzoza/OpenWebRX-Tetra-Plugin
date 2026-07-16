// DmrEpMetaPanel — panel OpenWebRX+ pokazujacy zdekodowane/odszyfrowane dane DMR EP.
// Author: SP8MB
// Wstrzykiwany do lib/MetaPanel.js przez patch_dmr_ep_panel.py; rejestrowany jako
// MetaPanel.types["dmrep"] i pokazywany dla trybu "dmr-ep" (DemodulatorPanel mapuje
// dmr-ep -> dmrep). Odbiera meta protocol="DMR" (isSupported).
//   - zdarzenia krypto / source->target  -> przewijany log w panelu dokowanym
//   - zdarzenia lrrp (pozycje GPS)        -> PLYWAJACE, przeciagane okno nad widmem
//     (te same zdarzenia co piny na mapie), tabela aktualizowana w miejscu.
function DmrEpMetaPanel(el) {
    MetaPanel.call(this, el);
    this.modes = ['DMR'];
    this.count = 0;
    this._lastKey = null;
    this.positions = {};   // "DMR-<id>" -> {lat, lon, speed, radius, ts, count}
    this._posWin = null;
    this._autoShown = false;
    var $el = $(el), self = this;

    // Przycisk otwarcia plywajacego okna pozycji (wstrzykiwany dynamicznie, bez index.html).
    var $list = $el.find('.openwebrx-dmrep-list');
    if ($list.length && !$el.find('.openwebrx-dmrpos-open').length) {
        $('<div style="padding:2px 4px;border-bottom:1px solid #666;">' +
          '<span class="openwebrx-dmrpos-open" title="Pokaz okno pozycji" ' +
          'style="cursor:pointer;color:#4fc3f7;">&#128205; Okno pozycji (LRRP) ' +
          '<span class="openwebrx-dmrpos-badge">0</span></span></div>').insertBefore($list);
    }

    $el.on('click', '.openwebrx-dmrep-clear', function () {
        $el.find('.openwebrx-dmrep-list').empty();
        self.count = 0;
        $el.find('.openwebrx-dmrep-count').text('0');
    });
    $el.on('click', '.openwebrx-dmrpos-open', function () { self._showPosWindow(); });
}

DmrEpMetaPanel.prototype = new MetaPanel();

DmrEpMetaPanel.prototype._time = function () {
    var d = new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
};

// ---- plywajace okno pozycji (wzorzec jak okno TETRA) ---------------------
DmrEpMetaPanel.prototype._ensurePosWindow = function () {
    if (this._posWin && document.body.contains(this._posWin)) return;
    var self = this;
    var win = document.createElement('div');
    win.className = 'openwebrx-dmrpos-window';
    win.style.cssText =
        'position:fixed;left:70px;top:70px;width:430px;max-width:92vw;background:#0b1622;' +
        'border:1px solid #345;border-radius:5px;box-shadow:0 4px 14px rgba(0,0,0,0.6);' +
        'z-index:10000;font-family:system-ui,sans-serif;font-size:12px;color:#cde;display:none';
    win.innerHTML =
        '<div class="dmrpos-title" style="cursor:move;padding:5px 8px;background:#12263a;' +
        'border-bottom:1px solid #345;border-radius:5px 5px 0 0;font-weight:bold;position:relative;">' +
        '&#128205; Pozycje DMR (LRRP) <span class="dmrpos-count" style="color:#9ccc65;">0</span>' +
        '<span class="dmrpos-close" title="Zamknij" style="position:absolute;top:0;right:0;width:30px;' +
        'height:26px;display:flex;align-items:center;justify-content:center;cursor:pointer;color:#fcc;' +
        'font-weight:bold;border-radius:0 5px 0 5px">&#10005;</span></div>' +
        '<div class="dmrpos-body" style="max-height:50vh;overflow:auto;padding:4px 6px;">' +
        '<table class="dmrpos-tbl" style="width:100%;border-collapse:collapse;font-size:12px;"></table>' +
        '<div class="dmrpos-empty" style="color:#789;padding:6px;">Czekam na raport pozycji (LRRP)…</div>' +
        '</div>';
    document.body.appendChild(win);
    this._posWin = win;

    win.querySelector('.dmrpos-close').addEventListener('click', function () {
        win.style.display = 'none';
    });

    // przeciaganie za pasek tytulu
    var title = win.querySelector('.dmrpos-title');
    var drag = null;
    title.addEventListener('mousedown', function (e) {
        if (e.target.className === 'dmrpos-close') return;
        drag = { ox: e.clientX - win.offsetLeft, oy: e.clientY - win.offsetTop };
        e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
        if (!drag) return;
        win.style.left = Math.max(0, e.clientX - drag.ox) + 'px';
        win.style.top = Math.max(0, e.clientY - drag.oy) + 'px';
    });
    document.addEventListener('mouseup', function () { drag = null; });
};

DmrEpMetaPanel.prototype._showPosWindow = function () {
    this._ensurePosWindow();
    this._posWin.style.display = '';
    this._renderPositions();
};

DmrEpMetaPanel.prototype._updatePosition = function (d) {
    var id = 'DMR-' + (d['source'] != null ? d['source'] : '?');
    var prev = this.positions[id];
    this.positions[id] = {
        lat: d['lat'], lon: d['lon'],
        speed: (d['speed'] != null ? d['speed'] : null),
        radius: (d['radius'] != null ? d['radius'] : null),
        ts: this._time(),
        count: (prev ? prev.count : 0) + 1
    };
    if (!this._autoShown) { this._autoShown = true; this._showPosWindow(); }
    this._renderPositions();
    var n = Object.keys(this.positions).length;
    $(this.el).find('.openwebrx-dmrpos-badge').text(n);
};

DmrEpMetaPanel.prototype._renderPositions = function () {
    if (!this._posWin) return;
    var self = this;
    var ids = Object.keys(this.positions).sort();
    var tbl = this._posWin.querySelector('.dmrpos-tbl');
    var empty = this._posWin.querySelector('.dmrpos-empty');
    var rows = '';
    ids.forEach(function (id) {
        var p = self.positions[id];
        var osm = 'https://www.openstreetmap.org/?mlat=' + p.lat + '&mlon=' + p.lon +
            '#map=14/' + p.lat + '/' + p.lon;
        var spd = (p.speed != null) ? p.speed.toFixed(1) + ' km/h' : '';
        rows +=
            '<tr>' +
            '<td style="padding:1px 5px;color:#4fc3f7;white-space:nowrap;">' + id + '</td>' +
            '<td style="padding:1px 5px;"><a href="' + osm + '" target="_blank" ' +
            'style="color:#9ccc65;">' + p.lat.toFixed(5) + ', ' + p.lon.toFixed(5) + '</a></td>' +
            '<td style="padding:1px 5px;text-align:right;white-space:nowrap;">' + spd + '</td>' +
            '<td style="padding:1px 5px;color:#999;white-space:nowrap;">' + p.ts + '</td>' +
            '</tr>';
    });
    tbl.innerHTML = rows;
    if (empty) empty.style.display = ids.length ? 'none' : '';
    this._posWin.querySelector('.dmrpos-count').textContent = ids.length;
};

DmrEpMetaPanel.prototype.update = function (data) {
    if (!this.isSupported(data)) return;

    // Pozycja LRRP -> plywajace okno pozycji (te same zdarzenia co piny na mapie).
    if (data['lrrp'] && data['lat'] != null && data['lon'] != null) {
        this._updatePosition(data);
        return;
    }

    var line = null, color = null;
    if (data['alg'] != null || data['decrypted'] != null) {
        // zdarzenie kryptograficzne (naglowek PI)
        if (data['decrypted']) {
            line = '🔓 odszyfrowano ' + (data['cipher'] || 'RC4') +
                ' ALG' + (data['alg'] || '?') + '/KID' + (data['keyid'] || '?');
            color = '#4caf50';
        } else if (data['encrypted']) {
            line = '🔒 zaszyfrowane (brak/zly klucz)';
            color = '#ff9800';
        }
    } else if (data['source'] && data['target']) {
        var ts = data['slot'] ? '[TS' + data['slot'] + '] ' : '';
        line = ts + data['source'] + ' → ' + data['target'] +
            (data['datatype'] ? ' · ' + data['datatype'] : '');
    }
    if (!line) return;

    // pomin natychmiastowe duplikaty (te same ramki powtarzaja sie)
    if (line === this._lastKey) return;
    this._lastKey = line;

    var row = $('<div class="openwebrx-dmrep-row"></div>').text(this._time() + '  ' + line);
    if (color) row.css('color', color);
    var list = $(this.el).find('.openwebrx-dmrep-list');
    list.prepend(row);
    var lrows = list.children();
    if (lrows.length > 200) lrows.slice(200).remove();
    this.count++;
    $(this.el).find('.openwebrx-dmrep-count').text(this.count);
};

// nie czyscimy logu/pozycji przy zmianie trybu — zostaw historie
DmrEpMetaPanel.prototype.clear = function () { };

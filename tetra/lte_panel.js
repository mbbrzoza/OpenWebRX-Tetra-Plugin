// LteMetaPanel — panel OpenWebRX+ trybu "LTE": identyfikacja kliknietej nosnej LTE.
// Author: SP8MB
// Wstrzykiwany do lib/MetaPanel.js przez patch_lte.py; rejestrowany jako
// MetaPanel.types["lte"] (div openwebrx-panel-metadata-lte). Odbiera meta protocol="LTE"
// z lte_decoder.py: status (analyzing/retune/...) i cell (PCI, MIB, SIB1..7).
function LteMetaPanel(el) {
    MetaPanel.call(this, el);
    this.modes = ['LTE'];
    this.history = {};     // "freq|pci" -> {freq, pci, plmn, ts}
    var $el = $(el), self = this;
    $el.on('click', '.openwebrx-lte-hclear', function () {
        self.history = {};
        self._renderHistory();
    });
}

LteMetaPanel.prototype = new MetaPanel();

LteMetaPanel.OPERATORS = {
    '260-01': 'Plus', '260-02': 'T-Mobile', '260-03': 'Orange', '260-06': 'Play',
    '260-17': 'Aero2', '260-34': 'NetWorkS!'
};

LteMetaPanel.prototype._esc = function (s) {
    return $('<div>').text(s == null ? '' : String(s)).html();
};

LteMetaPanel.prototype._mhz = function (hz) {
    return hz ? (hz / 1e6).toFixed(1) + ' MHz' : '?';
};

LteMetaPanel.prototype._time = function (ts) {
    var d = ts ? new Date(ts * 1000) : new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
};

LteMetaPanel.prototype._setStatus = function (text, color) {
    $(this.el).find('.openwebrx-lte-status').text(text).css('color', color || '#9ab');
};

LteMetaPanel.prototype._row = function (k, v) {
    return '<tr><td style="color:#8ab;padding-right:6px;white-space:nowrap;vertical-align:top">' +
        this._esc(k) + '</td><td>' + v + '</td></tr>';
};

LteMetaPanel.prototype._renderCell = function (d) {
    var e = this._esc.bind(this), rows = '';
    var s1 = d.sib1, mib = d.mib;
    var freq = this._mhz(d.freq);
    if (s1 && s1.earfcn) freq += ' · EARFCN ' + s1.earfcn;
    if (s1 && s1.band) freq += ' · B' + s1.band;
    rows += this._row('Częstotliwość', e(freq));
    rows += this._row('PCI', '<b>' + e(d.pci) + '</b> <span style="color:#789">(' + e(d.nid1) + '·3+' + e(d.nid2) + ')</span>');
    if (s1) {
        var ops = s1.plmn.map(function (p) {
            var n = LteMetaPanel.OPERATORS[p];
            return e(p) + (n ? ' <b>' + e(n) + '</b>' : '');
        }).join(', ');
        rows += this._row('Sieć (PLMN)', ops + (s1.reserved ? ' <span style="color:#fa0">zarezerw.</span>' : ''));
        rows += this._row('Cell ID', e(s1.cell_id) + ' <span style="color:#789">eNB ' + e(s1.enb_id) + ' / sektor ' + e(s1.sector) + '</span>');
        rows += this._row('TAC', e(s1.tac));
        var flags = [s1.tdd ? 'TDD' : 'FDD'];
        if (s1.barred) flags.push('<span style="color:#f66">ZABLOKOWANA</span>');
        if (s1.csg) flags.push('CSG');
        rows += this._row('Tryb', flags.join(' · '));
    }
    if (mib) {
        rows += this._row('Kanał', e((d.bw_mhz || '?') + ' MHz (' + mib.prb + ' PRB) · ' + mib.ports + ' port(y) TX'));
    }
    if (d.sib2) {
        var p = [];
        if (d.sib2.ref_signal_power != null) p.push('RS ' + d.sib2.ref_signal_power + ' dBm');
        if (d.sib2.ul) p.push('UL ' + d.sib2.ul.dl_mhz + ' MHz');
        if (d.sib2.ac_barring) p.push('<span style="color:#fa0">AC barring</span>');
        if (p.length) rows += this._row('SIB2', p.join(' · '));
    }
    var q = [];
    if (d.snr_db != null) q.push('SNR ≈ ' + d.snr_db + ' dB');
    q.push('CFO ' + d.cfo_hz + ' Hz');
    q.push('PSS ' + d.pss_score + '×');
    rows += this._row('Odbiór', e(q.join(' · ')));
    var self = this;
    if (d.lte_neighbours && d.lte_neighbours.length) {
        rows += this._row('Sąsiedzi LTE', d.lte_neighbours.map(function (n) {
            return e((n.band ? 'B' + n.band + ' ' : '') + (n.dl_mhz || '?') + ' MHz' +
                (n.bw_mhz ? ' (' + n.bw_mhz + ' MHz)' : '') + ' · EARFCN ' + n.earfcn +
                (n.priority != null ? ' · prio ' + n.priority : ''));
        }).join('<br>') + (d.serving_priority != null ? '<br><span style="color:#789">ta komórka: prio ' + e(d.serving_priority) + '</span>' : ''));
    }
    if (d.umts_neighbours && d.umts_neighbours.length) {
        rows += this._row('UMTS', d.umts_neighbours.map(function (n) {
            return e(n.dl_mhz + ' MHz · UARFCN ' + n.uarfcn);
        }).join('<br>'));
    }
    if (d.gsm_neighbours && d.gsm_neighbours.length) {
        rows += this._row('GSM', d.gsm_neighbours.map(function (n) {
            return e(n.band + (n.dl_mhz ? ' ' + n.dl_mhz[0] + '–' + n.dl_mhz[1] + ' MHz' : '') +
                ' · ' + n.arfcns.length + ' ARFCN');
        }).join('<br>'));
    }
    if (d.note) rows += this._row('', '<span style="color:#fa0">' + e(d.note) + '</span>');
    if (d.partial) rows += this._row('', '<span style="color:#9ab">dekodowanie MIB/SIB…</span>');
    $(this.el).find('.openwebrx-lte-cell').html('<table style="border-collapse:collapse">' + rows + '</table>');
};

LteMetaPanel.prototype._renderHistory = function () {
    var e = this._esc.bind(this), h = this.history;
    var keys = Object.keys(h).sort(function (a, b) { return h[b].ts - h[a].ts; });
    $(this.el).find('.openwebrx-lte-hcount').text(keys.length);
    $(this.el).find('.openwebrx-lte-history').html(keys.map(function (k) {
        var c = h[k];
        return '<div>' + e((c.freq / 1e6).toFixed(1) + ' MHz · PCI ' + c.pci + (c.plmn ? ' · ' + c.plmn : '')) + '</div>';
    }).join(''));
};

LteMetaPanel.prototype.update = function (data) {
    if (!this.isSupported(data)) return;
    if (data.type === 'status') {
        var st = {
            started: 'gotowy — kliknij nośną LTE', analyzing: 'analiza ' + this._mhz(data.freq) + '…',
            retune: 'przestrojono — nowa analiza', error: 'błąd: ' + (data.error || '?')
        }[data.state] || data.state;
        this._setStatus(st, data.state === 'error' ? '#f66' : null);
        if (data.state === 'started' && !data.pdsch) this._setStatus(st + ' (brak pdsch_ue: tylko PCI)', '#fa0');
        if (data.state === 'retune' || data.state === 'analyzing') {
            $(this.el).find('.openwebrx-lte-cell').css('opacity', 0.5);
        }
        return;
    }
    if (data.type !== 'cell') return;
    var cell = $(this.el).find('.openwebrx-lte-cell').css('opacity', 1);
    if (!data.found) {
        this._setStatus(this._time(data.ts) + '  ' + this._mhz(data.freq) + ': brak komórki LTE (PSS ' + data.pss_score + '×)', '#fa0');
        cell.html('');
        return;
    }
    this._setStatus(this._time(data.ts) + (data.partial ? '  PCI znaleziony' : '  gotowe w ' + data.took_s + ' s'), '#9ccc65');
    this._renderCell(data);
    var plmn = data.sib1 ? data.sib1.plmn.map(function (p) {
        return LteMetaPanel.OPERATORS[p] || p;
    }).join('/') : null;
    var key = data.freq + '|' + data.pci, prev = this.history[key];
    this.history[key] = {freq: data.freq, pci: data.pci, plmn: plmn || (prev && prev.plmn), ts: data.ts};
    this._renderHistory();
};

// historia zostaje przy zmianie trybu
LteMetaPanel.prototype.clear = function () { };

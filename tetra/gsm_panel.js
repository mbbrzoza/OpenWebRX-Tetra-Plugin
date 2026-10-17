// GsmMetaPanel — panel OpenWebRX+ trybu "GSM": identyfikacja kliknietej nosnej BCCH GSM.
// Author: SP8MB
// Wstrzykiwany do lib/MetaPanel.js przez patch_gsm.py; rejestrowany jako
// MetaPanel.types["gsm"] (div openwebrx-panel-metadata-gsm). Odbiera meta protocol="GSM"
// z gsm_decoder.py: status (analyzing/retune/...) i cell (BSIC, SI1..13).
function GsmMetaPanel(el) {
    MetaPanel.call(this, el);
    this.modes = ['GSM'];
    this.history = {};     // "arfcn|ci" -> {freq, arfcn, ci, plmn, ts}
    var $el = $(el), self = this;
    $el.on('click', '.openwebrx-gsm-hclear', function () {
        self.history = {};
        self._renderHistory();
    });
}

GsmMetaPanel.prototype = new MetaPanel();

GsmMetaPanel.OPERATORS = {
    '260-01': 'Plus', '260-02': 'T-Mobile', '260-03': 'Orange', '260-06': 'Play',
    '260-17': 'Aero2', '260-34': 'NetWorkS!'
};

GsmMetaPanel.prototype._esc = function (s) {
    return $('<div>').text(s == null ? '' : String(s)).html();
};

GsmMetaPanel.prototype._mhz = function (hz) {
    return hz ? (hz / 1e6).toFixed(1) + ' MHz' : '?';
};

GsmMetaPanel.prototype._time = function (ts) {
    var d = ts ? new Date(ts * 1000) : new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
};

GsmMetaPanel.prototype._setStatus = function (text, color) {
    $(this.el).find('.openwebrx-gsm-status').text(text).css('color', color || '#9ab');
};

GsmMetaPanel.prototype._row = function (k, v) {
    return '<tr><td style="color:#8ab;padding-right:6px;white-space:nowrap;vertical-align:top">' +
        this._esc(k) + '</td><td>' + v + '</td></tr>';
};

GsmMetaPanel.prototype._arfcns = function (list) {
    // ARFCN-y w zwartych zakresach: "2–12, 14, 15, 41–43"
    var out = [], i = 0;
    while (i < list.length) {
        var j = i;
        while (j + 1 < list.length && list[j + 1].arfcn === list[j].arfcn + 1) j++;
        out.push(j - i >= 2 ? list[i].arfcn + '–' + list[j].arfcn :
            list.slice(i, j + 1).map(function (a) { return a.arfcn; }).join(', '));
        i = j + 1;
    }
    return out.join(', ');
};

GsmMetaPanel.prototype._renderCell = function (d) {
    var e = this._esc.bind(this), rows = '', self = this;
    var freq = this._mhz(d.freq);
    if (d.arfcn != null) freq += ' · ARFCN ' + d.arfcn;
    if (d.band) freq += ' · ' + d.band;
    rows += this._row('Częstotliwość', e(freq));
    if (d.alerts) {
        var warns = d.alerts.filter(function (a) { return a.level === 'warn'; }).length;
        var head = warns >= 2 ? '<b style="color:#f66">⚠ PODEJRZANA (możliwy IMSI-catcher)</b>' :
            warns ? '<b style="color:#fa0">⚠ uwaga</b>' : '<span style="color:#9ccc65">✓ brak anomalii</span>';
        if (d.db_cells) head += ' <span style="color:#789">(baza: ' + d.db_cells + ' kom.)</span>';
        rows += this._row('Ocena', head + d.alerts.map(function (a) {
            return '<br><span style="color:' + (a.level === 'warn' ? '#fa0' : '#789') + '">' +
                (a.level === 'warn' ? '⚠ ' : 'ℹ ') + e(a.text) + '</span>';
        }).join(''));
    }
    if (d.reselect && d.reselect.cro_db) {
        rows += this._row('Reselekcja', e('CRO ' + d.reselect.cro_db + ' dB' +
            (d.reselect.temp_offset_db ? ' · TO ' + d.reselect.temp_offset_db + ' dB / ' + d.reselect.penalty_s + ' s' : '')));
    }
    if (d.plmn) {
        var n = GsmMetaPanel.OPERATORS[d.plmn];
        rows += this._row('Sieć (PLMN)', e(d.plmn) + (n ? ' <b>' + e(n) + '</b>' : ''));
    }
    if (d.ci != null) rows += this._row('CI', '<b>' + e(d.ci) + '</b> <span style="color:#789">(0x' + d.ci.toString(16) + ')</span>');
    if (d.lac != null) rows += this._row('LAC', e(d.lac) + ' <span style="color:#789">(0x' + d.lac.toString(16) + ')</span>');
    if (d.bsic) rows += this._row('BSIC', e(d.bsic.ncc + '-' + d.bsic.bcc) + ' <span style="color:#789">(NCC ' + e(d.bsic.ncc) + ', BCC ' + e(d.bsic.bcc) + ')</span>');
    var a = d.access;
    if (a) {
        var f = [];
        if (a.barred) f.push('<span style="color:#f66">ZABLOKOWANA</span>');
        if (a.emergency_barred) f.push('<span style="color:#fa0">alarmowe zablok.</span>');
        if (a.acc_barred && a.acc_barred.length) f.push('<span style="color:#fa0">AC zablok.: ' + e(a.acc_barred.join(',')) + '</span>');
        f.push('RXLEV min ' + e(a.rxlev_min_dbm) + ' dBm');
        if (a.ms_txpwr_max_dbm != null) f.push('MS max ' + e(a.ms_txpwr_max_dbm) + ' dBm');
        rows += this._row('Dostęp', f.join(' · '));
    }
    var c = d.control;
    if (c) {
        rows += this._row('Kanały', e(c.ccch + ' · AGCH res ' + c.agch_res + ' · PCH co ' + c.pa_mfrms + ' MF'));
        rows += this._row('Sieć rdzeniowa', e(c.mscr + (c.att ? ' · IMSI attach' : '') +
            (c.t3212_min ? ' · LU co ' + c.t3212_min + ' min' : '') + ' · RLT ' + c.rlt));
    }
    if (d.gprs) {
        var g = [];
        if (d.gprs.rac != null) g.push('RAC ' + d.gprs.rac);
        if (d.gprs.nmo) g.push('NMO ' + d.gprs.nmo);
        if (d.gprs.egprs) g.push('EDGE');
        rows += this._row('GPRS', e(g.length ? g.join(' · ') : 'tak'));
    }
    if (d.cell_alloc && d.cell_alloc.length) {
        rows += this._row('Alokacja (SI1)', e(this._arfcns(d.cell_alloc)) + ' <span style="color:#789">(' + d.cell_alloc.length + ' ARFCN)</span>');
    }
    var q = [];
    if (d.level_db != null) q.push('poziom ' + d.level_db + ' dB');
    if (d.cfo_hz != null) q.push('CFO ' + d.cfo_hz + ' Hz (' + d.cfo_ppm + ' ppm)');
    if (d.load) q.push('paging ' + d.load.paging_s + '/s · IA ' + d.load.imm_ass_s + '/s');
    rows += this._row('Odbiór', e(q.join(' · ')));
    if (d.gsm_neighbours && d.gsm_neighbours.length) {
        var g900 = d.gsm_neighbours.filter(function (n) { return n.arfcn < 512 || n.arfcn > 885; });
        var dcs = d.gsm_neighbours.filter(function (n) { return n.arfcn >= 512 && n.arfcn <= 885; });
        var s = [];
        if (g900.length) s.push('900: ' + this._arfcns(g900));
        if (dcs.length) s.push('1800: ' + this._arfcns(dcs));
        rows += this._row('Sąsiedzi GSM', e(s.join('\n')).replace(/\n/g, '<br>') +
            (d.ncc_permitted && d.ncc_permitted.length < 8 ? '<br><span style="color:#789">NCC: ' + e(d.ncc_permitted.join(',')) + '</span>' : ''));
    }
    if (d.umts_neighbours && d.umts_neighbours.length) {
        rows += this._row('UMTS', d.umts_neighbours.map(function (n) {
            return e(n.dl_mhz + ' MHz · UARFCN ' + n.uarfcn + (n.cells ? ' · ' + n.cells + ' kom.' : ''));
        }).join('<br>'));
    }
    if (d.lte_neighbours && d.lte_neighbours.length) {
        rows += this._row('LTE', d.lte_neighbours.map(function (n) {
            return e((n.band ? 'B' + n.band + ' ' : '') + (n.dl_mhz ? n.dl_mhz + ' MHz · ' : '') + 'EARFCN ' + n.earfcn +
                (n.priority != null ? ' · prio ' + n.priority : ''));
        }).join('<br>'));
    }
    if (d.si && d.si.length) rows += this._row('SI', '<span style="color:#789">' + e(d.si.join(' ')) + '</span>');
    if (d.note) rows += this._row('', '<span style="color:#fa0">' + e(d.note) + '</span>');
    $(this.el).find('.openwebrx-gsm-cell').html('<table style="border-collapse:collapse">' + rows + '</table>');
};

GsmMetaPanel.prototype._renderHistory = function () {
    var e = this._esc.bind(this), h = this.history;
    var keys = Object.keys(h).sort(function (a, b) { return h[b].ts - h[a].ts; });
    $(this.el).find('.openwebrx-gsm-hcount').text(keys.length);
    $(this.el).find('.openwebrx-gsm-history').html(keys.map(function (k) {
        var c = h[k];
        return '<div' + (c.warns >= 2 ? ' style="color:#f66"' : c.warns ? ' style="color:#fa0"' : '') + '>' +
            (c.warns ? '⚠ ' : '') + e((c.freq / 1e6).toFixed(1) + ' MHz' + (c.arfcn != null ? ' · ARFCN ' + c.arfcn : '') +
            (c.ci != null ? ' · CI ' + c.ci : '') + (c.plmn ? ' · ' + c.plmn : '')) + '</div>';
    }).join(''));
};

GsmMetaPanel.prototype.update = function (data) {
    if (!this.isSupported(data)) return;
    if (data.type === 'status') {
        var st = {
            started: 'gotowy — kliknij nośną BCCH GSM', analyzing: 'analiza ' + this._mhz(data.freq) + '…',
            retune: 'przestrojono — nowa analiza', error: 'błąd: ' + (data.error || '?')
        }[data.state] || data.state;
        this._setStatus(st, data.state === 'error' ? '#f66' : null);
        if (data.state === 'started' && !data.grgsm) this._setStatus(st + ' (brak gr-gsm: tylko CFO)', '#fa0');
        if (data.state === 'retune' || data.state === 'analyzing') {
            $(this.el).find('.openwebrx-gsm-cell').css('opacity', 0.5);
        }
        return;
    }
    if (data.type !== 'cell') return;
    var cell = $(this.el).find('.openwebrx-gsm-cell').css('opacity', 1);
    if (!data.found) {
        this._setStatus(this._time(data.ts) + '  ' + this._mhz(data.freq) + ': ' + (data.reason || 'brak komórki GSM'), '#fa0');
        cell.html('');
        return;
    }
    this._setStatus(this._time(data.ts) + '  gotowe w ' + data.took_s + ' s', '#9ccc65');
    this._renderCell(data);
    var plmn = data.plmn ? (GsmMetaPanel.OPERATORS[data.plmn] || data.plmn) : null;
    var key = data.arfcn + '|' + data.ci, prev = this.history[key];
    var warns = (data.alerts || []).filter(function (a) { return a.level === 'warn'; }).length;
    this.history[key] = {freq: data.freq, arfcn: data.arfcn, ci: data.ci, plmn: plmn || (prev && prev.plmn), ts: data.ts, warns: warns};
    this._renderHistory();
};

// historia zostaje przy zmianie trybu
GsmMetaPanel.prototype.clear = function () { };

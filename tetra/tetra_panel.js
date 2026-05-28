// TETRA meta panel for OpenWebRX+
// Author: SP8MB

function TetraMetaPanel(el) {
    MetaPanel.call(this, el);
    this.modes = ['TETRA'];
    this.networkNames = {
        '901-9999': 'Tetrapack'
    };
    // Group name lookup — extend as needed
    this.groupNames = {
        '91': 'tetraspot 91'
    };
    this.callTypeNames = {
        'individual': 'Indyw.',
        'group': 'Grupowe',
        'broadcast': 'Broadcast',
        'acknowledged group': 'Grupa potw.',
        'other': 'Inne'
    };
    this._activityLog = [];   // newest-first array of HTML row strings
    this._msRegLog = [];      // newest-first ms_register events
    this._sdsLog = [];        // newest-first SDS events
    this._seenSsis = {};      // ssi -> true (for "new" detection)
    this._currentCall = null; // {call_id, gssi, issis:Set, tx_ssi, timeslot, started_at}
    this._currentTimeslot = null;  // from latest burst timeslot data
    this._stash = {};         // dl_freq -> {activity, msReg, seenSsis, lastCarrier, lastNet}
    this._currentKey = null;  // current dl_freq key (string)
    this._labels = { gssi: {}, issi: {}, status: {} }; // user-defined labels
    this._priorities = {}; // gssi -> priority (0..5, 0 = none)
    this._lockouts = {};   // gssi -> true (ignore calls)
    this._soundsEnabled = true;
    this._durationTimer = null;
    this._gssiHold = '';   // if set, only show calls for this GSSI
    this._activeSsiPrev = {}; // ssi -> true, for "radio disappeared" detection
    this._ssiSeenAt = {};  // ssi -> first appearance Date.now() for TSI correlation
    this._terminalDb = {}; // ssi -> aggregated terminal info
    this._filters = {};    // event-kind -> bool (default true if missing)
    this._remoteWin = null;
    this._compact = false;
    this._loadStash();
    this._loadLabels();
    this._loadPrefs();
    var self = this;
    $(el).on('click', '.tetra-activity-toggle', function(){
        var listEl = $(el).find('.tetra-activity-list');
        var open = listEl.is(':visible');
        listEl.toggle(!open);
        $(el).find('.tetra-activity-arrow').text(open ? '▸' : '▾');
        $(el).find('.tetra-activity-clear').toggle(!open);
    });
    $(el).on('click', '.tetra-activity-clear', function(){
        self._activityLog = [];
        self._seenSsis = {};
        $(el).find('.tetra-activity-list').html('');
        $(el).find('.tetra-activity-count').text('0');
        self._stashCurrent();
    });
    $(el).on('click', '.tetra-open-ttt', function(){
        self._showTttWindow();
    });
    this._lastCarrier = null;
    this._lastNet = null;
}

// Set up prototype chain BEFORE adding methods, otherwise the assignment
// below would wipe them out.
TetraMetaPanel.prototype = new MetaPanel();

TetraMetaPanel.prototype._timestamp = function() {
    var d = new Date();
    var pad = function(n){ return n < 10 ? '0' + n : '' + n; };
    return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
};

TetraMetaPanel.prototype._formatTetraTime = function(tt) {
    if (!tt) return '---';
    var secs = tt.secs || 0;
    var pad = function(n){ return n < 10 ? '0' + n : '' + n; };
    var hh = Math.floor((secs % 86400) / 3600);
    var mm = Math.floor((secs % 3600) / 60);
    var ss = secs % 60;
    var day = Math.floor(secs / 86400);
    var off = tt.offset_min || 0;
    var offStr = 'UTC' + (off >= 0 ? '+' : '') + (off / 60).toFixed(off % 60 ? 2 : 0);
    return pad(hh) + ':' + pad(mm) + ':' + pad(ss) + ' (d' + day + ', ' + offStr + ', rok ' + (tt.year || '?') + ')';
};

TetraMetaPanel.prototype._logActivity = function(html, color) {
    var ts = this._timestamp();
    var row = '<div style="color:' + (color || '#bcd') + '">' +
              '<span style="color:#789">' + ts + '</span> ' + html + '</div>';
    this._activityLog.unshift(row);
    if (this._activityLog.length > 200) this._activityLog.length = 200;
    var el = $(this.el);
    el.find('.tetra-activity-list').html(this._activityLog.join(''));
    el.find('.tetra-activity-count').text(this._activityLog.length);
    if (this._tttWin && this._tttWin.style.display !== 'none') {
        this._tttWin.querySelector('.ttt-log').innerHTML = this._activityLog.join('');
        this._tttWin.querySelector('.ttt-log-count').textContent = '(' + this._activityLog.length + ')';
    }
    this._stashCurrent();
};

TetraMetaPanel.prototype._ensureTttWindow = function() {
    if (this._tttWin && document.body.contains(this._tttWin)) return;
    var self = this;
    var win = document.createElement('div');
    win.className = 'tetra-ttt-window';
    win.style.cssText = 'position:fixed;left:60px;top:60px;width:680px;max-width:90vw;'+
        'background:#0b1622;color:#cde;border:2px solid #345;border-radius:5px;'+
        'box-shadow:0 4px 14px rgba(0,0,0,0.6);z-index:10000;font-family:system-ui,sans-serif;font-size:0.9em;display:none';
    win.innerHTML = ''+
        '<div class="ttt-title" style="background:#1a3050;padding:4px 8px;cursor:move;display:flex;justify-content:space-between;align-items:center;border-radius:3px 3px 0 0">'+
        '  <div><b>TETRA Trunk Tracker</b> <span style="color:#9cf;margin-left:8px" class="ttt-net">—</span></div>'+
        '  <div>'+
        '    <span class="ttt-sound" style="cursor:pointer;color:#9ab;margin-right:8px" title="Włącz/wyłącz dźwięki">🔊</span>'+
        '    <span class="ttt-labels-btn" style="cursor:pointer;color:#9ab;margin-right:8px" title="Edytor etykiet G/SSI + priority/lockout">[etykiety]</span>'+
        '    <span class="ttt-filters-btn" style="cursor:pointer;color:#9ab;margin-right:8px" title="Filtry zdarzeń">[filtry]</span>'+
        '    <span class="ttt-hold-input" style="margin-right:8px;color:#9ab" title="GSSI Hold — pokaż tylko dla tej grupy">Hold: <input class="ttt-hold-val" style="width:64px;background:#021;color:#cde;border:1px solid #234;padding:1px 3px" placeholder="GSSI"></span>'+
        '    <span class="ttt-export" style="cursor:pointer;color:#9ab;margin-right:8px" title="Eksport CSV aktywnej zakładki">[CSV]</span>'+
        '    <span class="ttt-compact-btn" style="cursor:pointer;color:#9ab;margin-right:8px" title="Compact (F11)">[compact]</span>'+
        '    <span class="ttt-remote-btn" style="cursor:pointer;color:#9ab;margin-right:8px" title="Remote — duży display (F12)">[remote]</span>'+
        '    <span class="ttt-clear" style="cursor:pointer;color:#9ab;margin-right:10px" title="Wyczyść log">[wyczyść]</span>'+
        '    <span class="ttt-close" style="cursor:pointer;color:#fcc;font-weight:bold" title="Zamknij">✕</span>'+
        '  </div>'+
        '</div>'+
        '<div class="ttt-nowplaying" style="background:#021;border-bottom:1px solid #234;padding:3px 10px;font-family:monospace;font-size:0.85em;color:#9ab">NO CALL</div>'+
        '<div style="display:flex">'+
        '  <div class="ttt-left" style="flex:0 0 250px;padding:6px 8px;border-right:1px solid #234">'+
        '    <div style="font-size:0.8em;color:#9ab;margin-bottom:4px">Call details</div>'+
        '    <div><span class="ttt-status" style="display:inline-block;padding:2px 10px;border-radius:3px;background:#445;color:#aaa;font-weight:bold">Idle</span>'+
        '         <span class="ttt-call-type" style="margin-left:6px;color:#9cf"></span>'+
        '         <span class="ttt-duration" style="margin-left:6px;color:#51cf66;font-family:monospace"></span></div>'+
        '    <div style="margin-top:6px"><span style="color:#789">Call ID:</span> <b class="ttt-cid">—</b></div>'+
        '    <div><span style="color:#789">Carrier:</span> <b class="ttt-carrier">—</b></div>'+
        '    <div><span style="color:#789">Timeslot:</span> <b class="ttt-ts">—</b></div>'+
        '    <div style="margin-top:4px"><span style="color:#789">Grupa:</span> <b class="ttt-gssi">—</b></div>'+
        '    <div class="ttt-gname" style="color:#9cf;font-style:italic;min-height:18px"></div>'+
        '    <div style="margin-top:6px;color:#789">ISSI w rozmowie:</div>'+
        '    <div class="ttt-issis" style="border:1px solid #234;background:#021;padding:3px 5px;min-height:60px;max-height:120px;overflow-y:auto;font-family:monospace;font-size:0.9em;color:#cde;border-radius:2px">—</div>'+
        '    <div style="margin-top:6px"><span style="color:#789">TX SSI:</span> <b class="ttt-tx" style="color:#51cf66">—</b></div>'+
        '    <div class="ttt-time" style="margin-top:6px;color:#789;font-size:0.85em"></div>'+
        '  </div>'+
        '  <div class="ttt-right" style="flex:1;padding:6px 8px">'+
        '    <div style="display:flex;gap:4px;margin-bottom:4px;border-bottom:1px solid #234">'+
        '      <span class="ttt-tab ttt-tab-activity" data-tab="activity" style="cursor:pointer;padding:3px 10px;border-radius:3px 3px 0 0;background:#1a3050;color:#cde;font-size:0.85em">Aktywność <span class="ttt-log-count" style="color:#789">(0)</span></span>'+
        '      <span class="ttt-tab ttt-tab-msreg" data-tab="msreg" style="cursor:pointer;padding:3px 10px;border-radius:3px 3px 0 0;background:#0b1622;color:#9ab;font-size:0.85em">MS Rejestracje <span class="ttt-msreg-count" style="color:#789">(0)</span></span>'+
        '      <span class="ttt-tab ttt-tab-sds" data-tab="sds" style="cursor:pointer;padding:3px 10px;border-radius:3px 3px 0 0;background:#0b1622;color:#9ab;font-size:0.85em">SDS <span class="ttt-sds-count" style="color:#789">(0)</span></span>'+
        '      <span class="ttt-tab ttt-tab-dmo" data-tab="dmo" style="cursor:pointer;padding:3px 10px;border-radius:3px 3px 0 0;background:#0b1622;color:#9ab;font-size:0.85em" title="DM-MS direct mode signalling z pliku JSON (offline analiza)">DMO <span class="ttt-dmo-count" style="color:#789">(0)</span></span>'+
        '    </div>'+
        '    <div class="ttt-log" style="font-family:monospace;font-size:0.78em;color:#cde;height:330px;overflow-y:auto;border:1px solid #234;background:#021;padding:4px;border-radius:2px;line-height:1.4">brak zdarzeń</div>'+
        '    <div class="ttt-msreg-list" style="display:none;font-family:monospace;font-size:0.78em;color:#cde;height:330px;overflow-y:auto;border:1px solid #234;background:#021;padding:4px;border-radius:2px;line-height:1.4">brak rejestracji</div>'+
        '    <div class="ttt-sds-list" style="display:none;font-family:monospace;font-size:0.78em;color:#cde;height:330px;overflow-y:auto;border:1px solid #234;background:#021;padding:4px;border-radius:2px;line-height:1.4">brak SDS</div>'+
        '    <div class="ttt-dmo-list" style="display:none;font-family:monospace;font-size:0.78em;color:#cde;height:330px;overflow-y:auto;border:1px solid #234;background:#021;padding:4px;border-radius:2px;line-height:1.4"><div style="color:#789">załaduj <code>dmo_demo.json</code> przyciskiem [Load DMO JSON] (offline DMAC-SYNC PDU)</div><button class="ttt-dmo-load" style="margin-top:6px;background:#1a3050;border:1px solid #234;color:#cde;padding:3px 10px;cursor:pointer;border-radius:2px">Load DMO JSON</button></div>'+
        '  </div>'+
        '</div>';
    document.body.appendChild(win);
    this._tttWin = win;

    // Close + clear handlers
    win.querySelector('.ttt-close').addEventListener('click', function(){ win.style.display = 'none'; });
    win.querySelector('.ttt-clear').addEventListener('click', function(){
        var activeTab = win.querySelector('.ttt-tab[data-active="1"]');
        var tab = activeTab ? activeTab.getAttribute('data-tab') : 'activity';
        if (tab === 'msreg') {
            self._msRegLog = [];
            win.querySelector('.ttt-msreg-list').innerHTML = 'brak rejestracji';
            win.querySelector('.ttt-msreg-count').textContent = '(0)';
        } else if (tab === 'sds') {
            self._sdsLog = [];
            win.querySelector('.ttt-sds-list').innerHTML = 'brak SDS';
            win.querySelector('.ttt-sds-count').textContent = '(0)';
        } else {
            self._activityLog = [];
            win.querySelector('.ttt-log').innerHTML = 'brak zdarzeń';
            win.querySelector('.ttt-log-count').textContent = '(0)';
            $(self.el).find('.tetra-activity-list').html('');
            $(self.el).find('.tetra-activity-count').text('0');
        }
        self._stashCurrent();
    });
    win.querySelector('.ttt-dmo-load').addEventListener('click', function(){ self._loadDmoJson(); });
    win.querySelector('.ttt-labels-btn').addEventListener('click', function(){ self._showLabelsEditor(); });
    win.querySelector('.ttt-filters-btn').addEventListener('click', function(){ self._showFilterEditor(); });
    win.querySelector('.ttt-export').addEventListener('click', function(){ self._exportCsv(); });
    win.querySelector('.ttt-compact-btn').addEventListener('click', function(){ self._toggleCompact(); });
    win.querySelector('.ttt-remote-btn').addEventListener('click', function(){ self._showRemoteWindow(); });
    var holdInput = win.querySelector('.ttt-hold-val');
    holdInput.value = self._gssiHold || '';
    holdInput.addEventListener('change', function(){ self._gssiHold = holdInput.value.trim(); self._savePrefs(); self._renderTttWindow(); });
    holdInput.addEventListener('keydown', function(e){ if (e.key === 'Enter') { self._gssiHold = holdInput.value.trim(); self._savePrefs(); self._renderTttWindow(); } });
    var soundBtn = win.querySelector('.ttt-sound');
    var refreshSound = function(){ soundBtn.textContent = self._soundsEnabled ? '🔊' : '🔇'; soundBtn.style.color = self._soundsEnabled ? '#cde' : '#666'; };
    refreshSound();
    soundBtn.addEventListener('click', function(){ self._soundsEnabled = !self._soundsEnabled; self._savePrefs(); refreshSound(); });
    // Keyboard shortcuts F11 (compact) and F12 (remote) when TTT focused
    win.addEventListener('keydown', function(e){
        if (e.key === 'F11') { e.preventDefault(); self._toggleCompact(); }
        else if (e.key === 'F12') { e.preventDefault(); self._showRemoteWindow(); }
    });
    win.tabIndex = 0;

    // Tab switching
    var tabs = win.querySelectorAll('.ttt-tab');
    var setActive = function(name){
        tabs.forEach(function(t){
            var on = t.getAttribute('data-tab') === name;
            t.style.background = on ? '#1a3050' : '#0b1622';
            t.style.color = on ? '#cde' : '#9ab';
            t.setAttribute('data-active', on ? '1' : '0');
        });
        win.querySelector('.ttt-log').style.display = name === 'activity' ? 'block' : 'none';
        win.querySelector('.ttt-msreg-list').style.display = name === 'msreg' ? 'block' : 'none';
        win.querySelector('.ttt-sds-list').style.display = name === 'sds' ? 'block' : 'none';
        win.querySelector('.ttt-dmo-list').style.display = name === 'dmo' ? 'block' : 'none';
    };
    tabs.forEach(function(t){
        t.addEventListener('click', function(){
            setActive(t.getAttribute('data-tab'));
            // Auto-refresh DMO live view when tab opened
            if (t.getAttribute('data-tab') === 'dmo' && self._dmoLive && self._dmoLive.pdus && self._dmoLive.pdus.length) {
                self._renderDmoLive();
            }
        });
    });
    setActive('activity');

    // Draggable by title bar
    var title = win.querySelector('.ttt-title');
    var drag = null;
    title.addEventListener('mousedown', function(e){
        if (e.target.classList.contains('ttt-close') || e.target.classList.contains('ttt-clear')) return;
        drag = { ox: e.clientX - win.offsetLeft, oy: e.clientY - win.offsetTop };
        e.preventDefault();
    });
    document.addEventListener('mousemove', function(e){
        if (!drag) return;
        win.style.left = (e.clientX - drag.ox) + 'px';
        win.style.top = (e.clientY - drag.oy) + 'px';
    });
    document.addEventListener('mouseup', function(){ drag = null; });
};

TetraMetaPanel.prototype._showTttWindow = function() {
    this._ensureTttWindow();
    this._tttWin.style.display = 'block';
    this._renderTttWindow();
};

TetraMetaPanel.prototype._renderTttWindow = function() {
    if (!this._tttWin || this._tttWin.style.display === 'none') return;
    var w = this._tttWin;
    var c = this._currentCall;
    var statusEl = w.querySelector('.ttt-status');
    var self = this;
    if (c) {
        statusEl.textContent = c.status || 'Aktywne';
        statusEl.style.background = c.status === 'TX' ? '#2b8a3e' :
            c.status === 'Zestawienie' ? '#8a6d2b' :
            c.status === 'Aktywne' ? '#2b8a3e' : '#445';
        statusEl.style.color = '#fff';
        w.querySelector('.ttt-call-type').textContent = c.call_type ? '[' + c.call_type + ']' : '';
        w.querySelector('.ttt-duration').textContent = this._durationStr(c.started_at);
        w.querySelector('.ttt-cid').textContent = c.call_id || '—';
        w.querySelector('.ttt-ts').textContent = c.timeslot != null ? c.timeslot : '—';
        w.querySelector('.ttt-gssi').textContent = c.gssi || '—';
        var gn = c.gssi ? this._labelFor('gssi', c.gssi) : '';
        w.querySelector('.ttt-gname').textContent = gn;
        var issis = Array.from(c.issis || []);
        w.querySelector('.ttt-issis').innerHTML = issis.length
            ? issis.map(function(i){
                var isTx = c.tx_ssi && i === c.tx_ssi;
                var lbl = self._labelFor('issi', i);
                return '<div' + (isTx ? ' style="color:#51cf66;font-weight:bold"' : '') + '>' + i + (lbl ? ' <span style="color:#9cf">['+lbl+']</span>' : '') + '</div>';
            }).join('')
            : '—';
        var txLbl = c.tx_ssi ? this._labelFor('issi', c.tx_ssi) : '';
        w.querySelector('.ttt-tx').innerHTML = c.tx_ssi ? (c.tx_ssi + (txLbl ? ' <span style="color:#9cf">['+txLbl+']</span>' : '')) : '—';
        w.querySelector('.ttt-time').textContent = c.last_update || '';
        // Now Playing line
        var npParts = ['GSSI: ' + (c.gssi || '?')];
        if (gn) npParts.push('[' + gn + ']');
        if (c.tx_ssi) npParts.push('— TX: ' + c.tx_ssi + (txLbl ? ' [' + txLbl + ']' : ''));
        w.querySelector('.ttt-nowplaying').textContent = npParts.join(' ');
        w.querySelector('.ttt-nowplaying').style.color = c.status === 'TX' ? '#51cf66' : '#cde';
    } else {
        statusEl.textContent = 'Idle';
        statusEl.style.background = '#445';
        statusEl.style.color = '#aaa';
        w.querySelector('.ttt-call-type').textContent = '';
        w.querySelector('.ttt-duration').textContent = '';
        w.querySelector('.ttt-cid').textContent = '—';
        w.querySelector('.ttt-ts').textContent = '—';
        w.querySelector('.ttt-gssi').textContent = '—';
        w.querySelector('.ttt-gname').textContent = '';
        w.querySelector('.ttt-issis').innerHTML = '—';
        w.querySelector('.ttt-tx').textContent = '—';
        w.querySelector('.ttt-time').textContent = '';
        w.querySelector('.ttt-nowplaying').textContent = 'NO CALL';
        w.querySelector('.ttt-nowplaying').style.color = '#9ab';
    }
    // Carrier from netinfo (stored on instance)
    if (this._lastCarrier) w.querySelector('.ttt-carrier').textContent = this._lastCarrier;
    if (this._lastNet) w.querySelector('.ttt-net').textContent = this._lastNet;
    // Mirror activity log to right column
    w.querySelector('.ttt-log').innerHTML = this._activityLog.length ? this._activityLog.join('') : 'brak zdarzeń';
    w.querySelector('.ttt-log-count').textContent = '(' + this._activityLog.length + ')';
    w.querySelector('.ttt-msreg-list').innerHTML = this._msRegLog.length ? this._msRegLog.join('') : 'brak rejestracji';
    w.querySelector('.ttt-msreg-count').textContent = '(' + this._msRegLog.length + ')';
    w.querySelector('.ttt-sds-list').innerHTML = this._sdsLog.length ? this._sdsLog.join('') : 'brak SDS';
    w.querySelector('.ttt-sds-count').textContent = '(' + this._sdsLog.length + ')';
    this._renderRemote();
};

// ETSI TETRA enum ports (from decompiled SDRSharp.Tetra)
TetraMetaPanel.prototype._DISCONNECTION_CAUSES = [
    'Cause not defined or unknown', 'User requested disconnection', 'Called party busy',
    'Called party not reachable', 'Called party does not support encryption',
    'Congestion in infrastructure', 'Not allowed traffic case', 'Incompatible traffic case',
    'Requested service not available', 'Pre-emptive use of resource', 'Invalid call identifier',
    'Call rejected by the called party', 'No idle CC entity', 'Expiry of timer',
    'SwMI requested disconnection', 'Acknowledged service not completed', 'Unknown TETRA identity',
    'SS specific disconnection', 'Unknown external subscriber identity',
    'Call restoration of the other user failed', 'Called party requires encryption',
    'Concurrent setup not supported', 'Called party is under the same DM-GATE of the calling party',
    'Non-call-owner requested disconnection'
];
TetraMetaPanel.prototype._COMMUNICATION_TYPES = [
    'Individual', 'Group call', 'Point-to-multipoint Acknowledged', 'Broadcast'
];
TetraMetaPanel.prototype._SDS_PROTOCOL_IDENTS = {
    1: 'OTAK', 2: 'Simple text msg', 3: 'Simple location system', 4: 'Wireless datagram',
    5: 'Wireless control msg', 6: 'Managed DMO', 7: 'PIN authentication',
    8: 'End-to-end encrypted msg', 9: 'Simple immediate text', 10: 'Location information',
    11: 'Net Assist 2', 12: 'Concatenated SDS msg', 13: 'DOTAM', 14: 'Simple A-GNSS service'
};
TetraMetaPanel.prototype._CMCE_PDU_TYPES = [
    'D-Alert', 'D-Call-Proceeding', 'D-Connect', 'D-Connect-Acknowledge', 'D-Disconnect',
    'D-Info', 'D-Release', 'D-Setup', 'D-Status', 'D-TX-Ceased', 'D-TX-Continue',
    'D-TX-Granted', 'D-TX-Wait', 'D-TX-Interrupt', 'D-Call-Restore', 'D-SDS-Data', 'D-Facility'
];
TetraMetaPanel.prototype._DELIVERY_STATUS = {
    0: 'SDS receipt acknowledged by destination',
    1: 'SDS receipt report acknowledgement (SwMI source)',
    2: 'SDS consumed by destination',
    3: 'SDS consumed report acknowledgement (SwMI source)',
    4: 'SDS message forwarded to external network',
    5: 'SDS sent to group (acks prevented)',
    6: 'Concatenation part receipt acknowledged',
    32: 'Congestion — message stored by SwMI',
    33: 'Message stored by SwMI',
    34: 'Destination not reachable — message stored by SwMI',
    64: 'Network overload (temporary)',
    65: 'Service permanently not available on BS',
    66: 'Service temporary not available on BS',
    67: 'Source is not authorized for SDS',
    68: 'Destination is not authorized for SDS',
    69: 'Unknown destination gateway or service centre address',
    70: 'Unknown forward address',
    71: 'Group address with individual service',
    72: 'Validity period expired — message not received'
};

TetraMetaPanel.prototype._lookupDisconnectionCause = function(code) {
    var c = parseInt(code, 10);
    if (isNaN(c) || c < 0 || c >= this._DISCONNECTION_CAUSES.length) return null;
    return this._DISCONNECTION_CAUSES[c];
};

TetraMetaPanel.prototype._STASH_KEY = 'tetra_panel_stash_v1';
TetraMetaPanel.prototype._STASH_MAX_FREQS = 20;

TetraMetaPanel.prototype._loadStash = function() {
    try {
        var raw = localStorage.getItem(this._STASH_KEY);
        if (raw) this._stash = JSON.parse(raw) || {};
    } catch (e) { this._stash = {}; }
};

TetraMetaPanel.prototype._saveStash = function() {
    try {
        var keys = Object.keys(this._stash);
        if (keys.length > this._STASH_MAX_FREQS) {
            keys.sort(function(a,b){ return (this._stash[a].ts||0) - (this._stash[b].ts||0); }.bind(this));
            for (var i = 0; i < keys.length - this._STASH_MAX_FREQS; i++) delete this._stash[keys[i]];
        }
        localStorage.setItem(this._STASH_KEY, JSON.stringify(this._stash));
    } catch (e) {}
};

TetraMetaPanel.prototype._stashCurrent = function() {
    if (!this._currentKey) return;
    this._stash[this._currentKey] = {
        activity: this._activityLog.slice(0, 200),
        msReg: this._msRegLog.slice(0, 300),
        seenSsis: Object.assign({}, this._seenSsis),
        lastCarrier: this._lastCarrier,
        lastNet: this._lastNet,
        ts: Date.now()
    };
    this._saveStash();
};

TetraMetaPanel.prototype._switchFreq = function(newKey) {
    if (this._currentKey === newKey) return;
    this._stashCurrent();
    this._currentKey = newKey;
    var saved = this._stash[newKey];
    if (saved) {
        this._activityLog = (saved.activity || []).slice();
        this._msRegLog = (saved.msReg || []).slice();
        this._seenSsis = Object.assign({}, saved.seenSsis || {});
    } else {
        this._activityLog = [];
        this._msRegLog = [];
        this._seenSsis = {};
    }
    this._currentCall = null;
    this._currentTimeslot = null;
    var el = $(this.el);
    el.find('.tetra-activity-list').html(this._activityLog.join(''));
    el.find('.tetra-activity-count').text(this._activityLog.length);
    if (this._tttWin && this._tttWin.style.display !== 'none') {
        this._tttWin.querySelector('.ttt-log').innerHTML = this._activityLog.length ? this._activityLog.join('') : 'brak zdarzeń';
        this._tttWin.querySelector('.ttt-log-count').textContent = '(' + this._activityLog.length + ')';
        this._tttWin.querySelector('.ttt-msreg-list').innerHTML = this._msRegLog.length ? this._msRegLog.join('') : 'brak rejestracji';
        this._tttWin.querySelector('.ttt-msreg-count').textContent = '(' + this._msRegLog.length + ')';
    }
};

TetraMetaPanel.prototype._LABELS_KEY = 'tetra_labels_v1';
TetraMetaPanel.prototype._PREFS_KEY = 'tetra_prefs_v1';

TetraMetaPanel.prototype._loadLabels = function() {
    try {
        var raw = localStorage.getItem(this._LABELS_KEY);
        if (raw) {
            var d = JSON.parse(raw);
            this._labels.gssi = d.gssi || {};
            this._labels.issi = d.issi || {};
            this._labels.status = d.status || {};
        }
    } catch (e) {}
    // seed groupNames into labels
    for (var k in this.groupNames) if (!this._labels.gssi[k]) this._labels.gssi[k] = this.groupNames[k];
};

TetraMetaPanel.prototype._saveLabels = function() {
    try { localStorage.setItem(this._LABELS_KEY, JSON.stringify(this._labels)); } catch (e) {}
};

TetraMetaPanel.prototype._loadPrefs = function() {
    try {
        var raw = localStorage.getItem(this._PREFS_KEY);
        if (raw) {
            var d = JSON.parse(raw);
            if (typeof d.sounds === 'boolean') this._soundsEnabled = d.sounds;
            if (typeof d.gssiHold === 'string') this._gssiHold = d.gssiHold;
            this._priorities = d.priorities || {};
            this._lockouts = d.lockouts || {};
            this._filters = d.filters || {};
        }
    } catch (e) {}
};

TetraMetaPanel.prototype._savePrefs = function() {
    try { localStorage.setItem(this._PREFS_KEY, JSON.stringify({
        sounds: this._soundsEnabled,
        gssiHold: this._gssiHold,
        priorities: this._priorities,
        lockouts: this._lockouts,
        filters: this._filters
    })); } catch (e) {}
};

TetraMetaPanel.prototype._filterAllows = function(kind) {
    return this._filters[kind] !== false; // default allow
};

TetraMetaPanel.prototype._labelFor = function(kind, id) {
    if (id == null) return '';
    var v = this._labels[kind] && this._labels[kind][String(id)];
    return v || '';
};

TetraMetaPanel.prototype._statusText = function(code) {
    var hex = '0x' + code.toString(16).toUpperCase().padStart(4, '0');
    var lbl = this._labelFor('status', code) || this._labelFor('status', hex);
    // TETRA reserved well-known codes (ETSI EN 300 392-2)
    var wellKnown = {
        0x8000: 'Emergency call (urgent)',
        0x8001: 'Test call',
        0x8002: 'Call back',
        0x8003: 'Call requested',
        0x8004: 'Routine call',
        0x8005: 'Acknowledge',
        0x8006: 'OK / Affirmative',
        0x8007: 'Wait',
        0x8008: 'Repeat last',
        0x8009: 'Negative',
        0x800A: 'Yes',
        0x800B: 'No'
    };
    if (!lbl && wellKnown[code]) lbl = wellKnown[code];
    return hex + (lbl ? ' — ' + lbl : '');
};

TetraMetaPanel.prototype._playSound = function(kind) {
    if (!this._soundsEnabled) return;
    try {
        var ctx = this._audioCtx || (this._audioCtx = new (window.AudioContext || window.webkitAudioContext)());
        var freq = kind === 'setup' ? 880 : kind === 'release' ? 440 : kind === 'tx' ? 660 : 550;
        var dur = 0.15;
        var o = ctx.createOscillator();
        var g = ctx.createGain();
        o.frequency.value = freq;
        o.type = 'sine';
        g.gain.setValueAtTime(0.0001, ctx.currentTime);
        g.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + dur);
        o.connect(g); g.connect(ctx.destination);
        o.start();
        o.stop(ctx.currentTime + dur);
    } catch (e) {}
};

TetraMetaPanel.prototype._durationStr = function(startTs) {
    if (!startTs) return '';
    var s = Math.floor((Date.now() - startTs) / 1000);
    var mm = Math.floor(s / 60), ss = s % 60;
    return (mm < 10 ? '0' : '') + mm + ':' + (ss < 10 ? '0' : '') + ss;
};

TetraMetaPanel.prototype._startDurationTimer = function() {
    if (this._durationTimer) return;
    var self = this;
    this._durationTimer = setInterval(function(){
        if (!self._currentCall) { self._stopDurationTimer(); return; }
        var s = self._durationStr(self._currentCall.started_at);
        if (self._tttWin && self._tttWin.style.display !== 'none') {
            var d = self._tttWin.querySelector('.ttt-duration');
            if (d) d.textContent = s;
        }
        if (self._remoteWin && self._remoteWin.style.display !== 'none') {
            var rd = self._remoteWin.querySelector('.rem-duration');
            if (rd) rd.textContent = s;
        }
    }, 1000);
};

TetraMetaPanel.prototype._stopDurationTimer = function() {
    if (this._durationTimer) { clearInterval(this._durationTimer); this._durationTimer = null; }
};

TetraMetaPanel.prototype._addSdsEvent = function(data) {
    var ts = this._timestamp();
    var parts = [];
    var color = '#9cf';
    if (data.protocol_ident != null) {
        var pn = this._SDS_PROTOCOL_IDENTS[data.protocol_ident];
        parts.push('PI:' + data.protocol_ident + (pn ? ' (' + pn + ')' : ''));
    }
    if (data.status_code != null) {
        parts.push('status: ' + this._statusText(data.status_code));
        color = '#fc9';
    }
    if (data.delivery_status != null) {
        var ds = this._DELIVERY_STATUS[data.delivery_status];
        parts.push('delivery:' + data.delivery_status + (ds ? ' — ' + ds : ''));
        color = '#ffd43b';
    }
    if (data.src_ssi) {
        var sl = this._labelFor('issi', data.src_ssi);
        parts.push('src: ' + data.src_ssi + (sl ? ' [' + sl + ']' : ''));
    }
    if (data.dest_ssi) {
        var dl = this._labelFor('issi', data.dest_ssi);
        parts.push('dest: ' + data.dest_ssi + (dl ? ' [' + dl + ']' : ''));
    }
    if (data.text) {
        parts.push('"' + data.text.replace(/</g,'&lt;').substring(0,180) + '"');
    } else if (!parts.length && data.descr) {
        parts.push(data.descr.replace(/</g,'&lt;').substring(0,200));
    }
    var row = '<div style="color:' + color + '">' +
              '<span style="color:#789">' + ts + '</span> ' +
              '<b>SDS</b> · ' + parts.join(' · ') +
              '</div>';
    this._sdsLog.unshift(row);
    if (this._sdsLog.length > 300) this._sdsLog.length = 300;
    if (this._tttWin && this._tttWin.style.display !== 'none') {
        this._tttWin.querySelector('.ttt-sds-list').innerHTML = this._sdsLog.join('');
        this._tttWin.querySelector('.ttt-sds-count').textContent = '(' + this._sdsLog.length + ')';
    }
};

TetraMetaPanel.prototype._FILTER_GROUPS = [
    { title: 'Połączenia', items: [
        ['call_setup', 'Call Setup'], ['call_connect', 'Call Connect'],
        ['call_release', 'Call Release (D-Release)'], ['call_disconnect', 'Call Disconnect (D-Disconnect)'],
        ['call_alert', 'Call Alert (D-Alert)'], ['call_proceeding', 'Call Proceeding'],
        ['connect_ack', 'Connect ACK'], ['call_info', 'Call Info'], ['call_restore', 'Call Restore']
    ]},
    { title: 'Transmisja TX', items: [
        ['tx_grant', 'TX Grant'], ['tx_ceased', 'TX Ceased'], ['tx_continue', 'TX Continue'],
        ['tx_interrupt', 'TX Interrupt'], ['tx_wait', 'TX Wait']
    ]},
    { title: 'Komórki / Handover', items: [
        ['cell_change_new_cell', 'D-New-Cell (handover)'],
        ['cell_change_prepare_fail', 'D-Prepare-Fail'],
        ['cell_change_restore_ack', 'D-Restore-ACK'],
        ['cell_change_restore_fail', 'D-Restore-Fail'],
        ['cell_change_channel_response', 'D-Channel-Response'],
        ['cell_change_nwrk_broadcast_ext', 'D-Nwrk-Broadcast-Ext'],
        ['facility', 'D-Facility']
    ]},
    { title: 'Aktywne SSI', items: [
        ['ssi_appeared', 'SSI pojawił się'], ['ssi_disappeared', 'SSI zniknął (TTL)']
    ]},
    { title: 'MS Rejestracja (zakładka)', items: [
        ['location_update_accept', 'LU Accept'], ['location_update_reject', 'LU Reject'],
        ['location_update_command', 'LU Command'], ['location_update_proceeding', 'LU Proceeding'],
        ['group_attach', 'Group Attach'], ['group_detach', 'Group Detach'],
        ['attach_detach_ack', 'Attach/Detach ACK'],
        ['authentication_demand', 'Auth Demand'], ['authentication_result', 'Auth Result'],
        ['otar', 'OTAR (key update)'], ['ck_change_demand', 'CK Change Demand'],
        ['ms_disable', '🚫 MS Disable'], ['ms_enable', '✓ MS Enable'],
        ['mm_status', 'MM Status']
    ]},
    { title: 'SDS', items: [
        ['sds', 'SDS (zakładka)']
    ]}
];

TetraMetaPanel.prototype._showFilterEditor = function() {
    var self = this;
    var existing = document.querySelector('.tetra-filter-modal');
    if (existing) existing.remove();
    var modal = document.createElement('div');
    modal.className = 'tetra-filter-modal';
    modal.style.cssText = 'position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);width:560px;max-height:82vh;'+
        'background:#0b1622;border:2px solid #345;border-radius:5px;z-index:10001;color:#cde;font-family:system-ui,sans-serif;'+
        'font-size:0.9em;box-shadow:0 4px 18px rgba(0,0,0,0.7);display:flex;flex-direction:column';
    var groupsHtml = this._FILTER_GROUPS.map(function(grp){
        var rows = grp.items.map(function(it){
            var k = it[0], lbl = it[1], on = self._filterAllows(k);
            return '<label style="display:block;padding:1px 4px"><input type="checkbox" data-flt="'+k+'"'+(on?' checked':'')+' style="margin-right:6px">'+lbl+'</label>';
        }).join('');
        return '<div style="flex:1;min-width:240px;margin:6px"><div style="color:#9ab;font-weight:bold;border-bottom:1px solid #234;padding-bottom:3px;margin-bottom:3px">'+grp.title+'</div>'+rows+'</div>';
    }).join('');
    modal.innerHTML = ''+
        '<div style="background:#1a3050;padding:5px 10px;display:flex;justify-content:space-between;align-items:center;border-radius:3px 3px 0 0">'+
        '  <b>Filtry zdarzeń</b>'+
        '  <span class="flt-close" style="cursor:pointer;color:#fcc;font-weight:bold">✕</span>'+
        '</div>'+
        '<div style="padding:6px 10px;border-bottom:1px solid #234">'+
        '  <span class="flt-all" style="cursor:pointer;padding:2px 8px;background:#2b5050;border-radius:3px;margin-right:6px">Zaznacz wszystko</span>'+
        '  <span class="flt-none" style="cursor:pointer;padding:2px 8px;background:#503030;border-radius:3px">Odznacz wszystko</span>'+
        '</div>'+
        '<div style="overflow-y:auto;padding:6px;flex:1;display:flex;flex-wrap:wrap">'+groupsHtml+'</div>'+
        '<div style="padding:6px 10px;border-top:1px solid #234;text-align:right">'+
        '  <span class="flt-save" style="cursor:pointer;padding:4px 14px;background:#2b8a3e;color:#fff;border-radius:3px">Zapisz</span>'+
        '</div>';
    document.body.appendChild(modal);
    modal.querySelector('.flt-close').addEventListener('click', function(){ modal.remove(); });
    modal.querySelector('.flt-all').addEventListener('click', function(){
        modal.querySelectorAll('input[data-flt]').forEach(function(cb){ cb.checked = true; });
    });
    modal.querySelector('.flt-none').addEventListener('click', function(){
        modal.querySelectorAll('input[data-flt]').forEach(function(cb){ cb.checked = false; });
    });
    modal.querySelector('.flt-save').addEventListener('click', function(){
        modal.querySelectorAll('input[data-flt]').forEach(function(cb){
            self._filters[cb.getAttribute('data-flt')] = cb.checked;
        });
        self._savePrefs();
        modal.remove();
    });
};

TetraMetaPanel.prototype._touchTerminal = function(ssi, src, extra) {
    if (!ssi) return null;
    var key = String(ssi);
    var t = this._terminalDb[key];
    if (!t) {
        t = this._terminalDb[key] = {
            ssi: key, firstSeen: Date.now(), lastSeen: Date.now(),
            sources: {}, groups: {}, calls: {}, encr: null, subscr_class: null,
            la: null, txCount: 0, lastCallId: null, lastAction: null
        };
    }
    t.lastSeen = Date.now();
    if (src) t.sources[src] = (t.sources[src] || 0) + 1;
    if (extra) {
        if (extra.gssi) t.groups[String(extra.gssi)] = true;
        if (extra.call_id != null) { t.calls[String(extra.call_id)] = true; t.lastCallId = extra.call_id; }
        if (extra.encr != null) t.encr = extra.encr;
        if (extra.subscr_class != null) t.subscr_class = extra.subscr_class;
        if (extra.la != null && extra.la !== '0') t.la = extra.la;
        if (extra.tx) t.txCount++;
        if (extra.action) t.lastAction = extra.action;
    }
    return t;
};

TetraMetaPanel.prototype._renderTerminalSummary = function(ssi) {
    var t = this._terminalDb[String(ssi)];
    if (!t) return '';
    var self = this;
    var fmt = function(ts){
        var d = new Date(ts), pad = function(n){ return n<10 ? '0'+n : ''+n; };
        return pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds());
    };
    var label = this._labelFor('issi', ssi);
    var encStr = t.encr === 2 ? '🔒 enabled' : (t.encr === 1 ? 'clear' : t.encr === 0 ? '?' : '—');
    var groups = Object.keys(t.groups).map(function(g){
        var gl = self._labelFor('gssi', g);
        return g + (gl ? ' [' + gl + ']' : '');
    });
    var sources = Object.keys(t.sources).sort();
    var lines = [];
    if (label) lines.push('etykieta: <b>' + label + '</b>');
    lines.push('first: ' + fmt(t.firstSeen) + ' · last: ' + fmt(t.lastSeen));
    lines.push('encr: ' + encStr);
    if (t.la) lines.push('LA: ' + t.la);
    if (t.subscr_class != null) lines.push('class: 0x' + t.subscr_class.toString(16));
    if (groups.length) lines.push('grupy: ' + groups.join(', '));
    if (Object.keys(t.calls).length) lines.push('calls: ' + Object.keys(t.calls).length + (t.lastCallId != null ? ' (last:' + t.lastCallId + ')' : ''));
    if (t.txCount) lines.push('TX count: ' + t.txCount);
    if (sources.length) lines.push('źródła: ' + sources.join(', '));
    return '<div style="margin-left:18px;color:#789;font-size:0.92em;border-left:2px solid #345;padding-left:6px">'+lines.join(' · ')+'</div>';
};

TetraMetaPanel.prototype._showLabelsEditor = function() {
    var self = this;
    var existing = document.querySelector('.tetra-labels-modal');
    if (existing) { existing.remove(); }
    var modal = document.createElement('div');
    modal.className = 'tetra-labels-modal';
    modal.style.cssText = 'position:fixed;left:50%;top:50%;transform:translate(-50%,-50%);width:520px;max-height:80vh;'+
        'background:#0b1622;border:2px solid #345;border-radius:5px;z-index:10001;color:#cde;font-family:system-ui,sans-serif;'+
        'font-size:0.9em;box-shadow:0 4px 18px rgba(0,0,0,0.7);display:flex;flex-direction:column';
    var seenG = new Set(Object.keys(this._labels.gssi));
    var seenI = new Set(Object.keys(this._labels.issi));
    if (this._currentCall) {
        if (this._currentCall.gssi) seenG.add(String(this._currentCall.gssi));
        (this._currentCall.issis || []).forEach(function(i){ seenI.add(String(i)); });
    }
    for (var s in this._seenSsis) seenI.add(s);
    var gRows = Array.from(seenG).sort().map(function(g){
        var prio = self._priorities[g] || 0;
        var lock = !!self._lockouts[g];
        return '<tr><td style="padding:2px 6px;color:#9cf">'+g+'</td>'+
               '<td style="padding:2px 6px"><input data-kind="gssi" data-id="'+g+'" value="'+(self._labels.gssi[g]||'').replace(/"/g,'&quot;')+'" style="width:100%;background:#021;color:#cde;border:1px solid #234;padding:2px 4px"></td>'+
               '<td style="padding:2px 6px"><select data-prio="'+g+'" style="background:#021;color:#cde;border:1px solid #234">'+
                 [0,1,2,3,4,5].map(function(p){ return '<option value="'+p+'"'+(p===prio?' selected':'')+'>'+(p||'-')+'</option>'; }).join('')+
               '</select></td>'+
               '<td style="padding:2px 6px;text-align:center"><input type="checkbox" data-lock="'+g+'"'+(lock?' checked':'')+'></td></tr>';
    }).join('');
    var iRows = Array.from(seenI).sort().map(function(i){
        return '<tr><td style="padding:2px 6px;color:#9cf">'+i+'</td>'+
               '<td style="padding:2px 6px"><input data-kind="issi" data-id="'+i+'" value="'+(self._labels.issi[i]||'').replace(/"/g,'&quot;')+'" style="width:100%;background:#021;color:#cde;border:1px solid #234;padding:2px 4px"></td></tr>';
    }).join('');
    modal.innerHTML = ''+
        '<div style="background:#1a3050;padding:5px 10px;display:flex;justify-content:space-between;align-items:center;border-radius:3px 3px 0 0">'+
        '  <b>G/SSI Labels Editor</b>'+
        '  <span class="lbl-close" style="cursor:pointer;color:#fcc;font-weight:bold">✕</span>'+
        '</div>'+
        '<div style="overflow-y:auto;padding:8px 10px;flex:1">'+
        '  <div style="display:flex;gap:10px">'+
        '    <div style="flex:1">'+
        '      <div style="color:#9ab;margin-bottom:4px">GSSIs <span style="color:#789">('+seenG.size+')</span> · prio (0-5) · lock</div>'+
        '      <table style="width:100%;border-collapse:collapse;font-size:0.85em"><thead><tr style="color:#678"><th>GSSI</th><th>etykieta</th><th>P</th><th>🔒</th></tr></thead>'+(gRows || '<tr><td style="color:#678">brak</td></tr>')+'</table>'+
        '    </div>'+
        '    <div style="flex:1">'+
        '      <div style="color:#9ab;margin-bottom:4px">ISSIs <span style="color:#789">('+seenI.size+')</span></div>'+
        '      <table style="width:100%;border-collapse:collapse;font-size:0.85em">'+(iRows || '<tr><td style="color:#678">brak</td></tr>')+'</table>'+
        '    </div>'+
        '  </div>'+
        '</div>'+
        '<div style="padding:6px 10px;border-top:1px solid #234;text-align:right">'+
        '  <span class="lbl-save" style="cursor:pointer;padding:4px 12px;background:#2b8a3e;color:#fff;border-radius:3px">Zapisz</span>'+
        '</div>';
    document.body.appendChild(modal);
    modal.querySelector('.lbl-close').addEventListener('click', function(){ modal.remove(); });
    modal.querySelector('.lbl-save').addEventListener('click', function(){
        modal.querySelectorAll('input[data-kind]').forEach(function(inp){
            var k = inp.getAttribute('data-kind'), id = inp.getAttribute('data-id'), v = inp.value.trim();
            if (v) self._labels[k][id] = v; else delete self._labels[k][id];
        });
        modal.querySelectorAll('select[data-prio]').forEach(function(sel){
            var id = sel.getAttribute('data-prio'), p = parseInt(sel.value, 10) || 0;
            if (p) self._priorities[id] = p; else delete self._priorities[id];
        });
        modal.querySelectorAll('input[data-lock]').forEach(function(cb){
            var id = cb.getAttribute('data-lock');
            if (cb.checked) self._lockouts[id] = true; else delete self._lockouts[id];
        });
        self._saveLabels();
        self._savePrefs();
        modal.remove();
        self._renderTttWindow();
    });
};

TetraMetaPanel.prototype._isLockedOut = function(gssi) {
    return gssi != null && !!this._lockouts[String(gssi)];
};

TetraMetaPanel.prototype._isHoldFiltered = function(gssi) {
    if (!this._gssiHold) return false;
    return String(gssi) !== this._gssiHold;
};

// Embedded DMO demo snapshot — wygenerowane przez:
//   python3 dmo_parse_to_json.py test_data/dmo_bursts.bin /tmp/dmo_demo.json
// z 47 burstów DMO IQ z 433.400 (Tetrapack). Pełen offline dekod ramki signaling.
TetraMetaPanel.prototype._DMO_DEMO_DATA = {"meta":{"source":"test_data/dmo_bursts.bin","total_bursts":47,"sch_s_ok":12,"sch_h_ok":9,"both_ok":8},"stats":{"unique_src_ssi":{"2600824":8},"unique_dst_ssi":{"20":8},"message_types":{"DM-OCCUPIED":2,"DM-TX CEASED":1,"DM-RESERVED":5},"mni_seen":{"901-9999":8}},"pdus":[{"burst_idx":1,"both_ok":true,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=1 FN=18 enc=clear msg=DM-OCCUPIED src=2600824 dst=20 MNI=901-9999","msg":"DM-OCCUPIED","src":2600824,"dst":20,"mcc":901,"mnc":9999,"tn":1,"fn":18},{"burst_idx":2,"both_ok":false,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=18 enc=clear","msg":"","src":null,"dst":null,"mcc":null,"mnc":null,"tn":3,"fn":18},{"burst_idx":4,"both_ok":true,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=12 enc=clear msg=DM-OCCUPIED src=2600824 dst=20 MNI=901-9999","msg":"DM-OCCUPIED","src":2600824,"dst":20,"mcc":901,"mnc":9999,"tn":3,"fn":12},{"burst_idx":5,"both_ok":true,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=15 enc=clear msg=DM-TX CEASED src=2600824 dst=20 MNI=901-9999","msg":"DM-TX CEASED","src":2600824,"dst":20,"mcc":901,"mnc":9999,"tn":3,"fn":15},{"burst_idx":7,"both_ok":true,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=1 FN=18 enc=clear msg=DM-RESERVED src=2600824 dst=20 MNI=901-9999","msg":"DM-RESERVED","src":2600824,"dst":20,"mcc":901,"mnc":9999,"tn":1,"fn":18},{"burst_idx":8,"both_ok":false,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=18 enc=clear","msg":"","src":null,"dst":null,"mcc":null,"mnc":null,"tn":3,"fn":18},{"burst_idx":10,"both_ok":true,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=6 enc=clear msg=DM-RESERVED src=2600824 dst=20 MNI=901-9999","msg":"DM-RESERVED","src":2600824,"dst":20,"mcc":901,"mnc":9999,"tn":3,"fn":6},{"burst_idx":11,"both_ok":true,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=12 enc=clear msg=DM-RESERVED src=2600824 dst=20 MNI=901-9999","msg":"DM-RESERVED","src":2600824,"dst":20,"mcc":901,"mnc":9999,"tn":3,"fn":12},{"burst_idx":12,"both_ok":true,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=18 enc=clear msg=DM-RESERVED src=2600824 dst=20 MNI=901-9999","msg":"DM-RESERVED","src":2600824,"dst":20,"mcc":901,"mnc":9999,"tn":3,"fn":18},{"burst_idx":22,"both_ok":false,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=18 enc=clear","msg":"","src":null,"dst":null,"mcc":null,"mnc":null,"tn":3,"fn":18},{"burst_idx":24,"both_ok":false,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=12 enc=clear","msg":"","src":null,"dst":null,"mcc":null,"mnc":null,"tn":3,"fn":12},{"burst_idx":25,"both_ok":true,"summary":"[DMAC-SYNC] sys=0xd comm=MS-MS  TN=3 FN=18 enc=clear msg=DM-RESERVED src=2600824 dst=20 MNI=901-9999","msg":"DM-RESERVED","src":2600824,"dst":20,"mcc":901,"mnc":9999,"tn":3,"fn":18}]};

TetraMetaPanel.prototype._loadDmoJson = function() {
    this._renderDmoData(this._DMO_DEMO_DATA, 'embedded snapshot (test_data/dmo_bursts.bin, 47 burstów z 433.400 MHz Tetrapack)');
};

TetraMetaPanel.prototype._renderDmoLive = function() {
    var list = this._tttWin && this._tttWin.querySelector('.ttt-dmo-list');
    if (!list || !this._dmoLive) return;
    var dl = this._dmoLive;
    var pdus = dl.pdus || [];
    if (!pdus.length) return;  // nothing yet, leave initial placeholder
    var stats = dl.stats || {};
    var lastStats = dl.lastStats || {};

    var html = '';
    html += '<div style="color:#789;font-size:0.85em;margin-bottom:6px">źródło: <b style="color:#51cf66">LIVE</b> (z tetra_dmo_decoder.py) <button class="ttt-dmo-load" style="margin-left:6px;background:#1a3050;border:1px solid #234;color:#cde;padding:1px 6px;cursor:pointer;border-radius:2px;font-size:0.85em">demo static</button></div>';
    html += '<div style="background:#021;border:1px solid #234;padding:4px 6px;margin-bottom:6px;border-radius:2px">';
    html += '<div><b style="color:#51cf66">Statystyki live:</b></div>';
    var sst = lastStats.sch_s_total || 0;
    var sso = lastStats.sch_s_ok || 0;
    var sho = lastStats.sch_h_ok || 0;
    html += '<div>SCH/S CRC: <b>' + sso + '/' + sst + '</b>';
    if (sst > 0) html += ' (' + (100*sso/sst).toFixed(1) + '%)';
    html += '  |  SCH/H CRC OK: <b>' + sho + '</b>  |  PDU total: <b>' + stats.n_pdus + '</b></div>';
    html += '</div>';
    var renderDict = function(title, dict) {
        if (!dict || !Object.keys(dict).length) return '';
        var lines = Object.keys(dict).map(function(k){ return k + ' (' + dict[k] + '×)'; });
        return '<div><span style="color:#789">' + title + ':</span> ' + lines.join(', ') + '</div>';
    };
    html += '<div style="background:#021;border:1px solid #234;padding:4px 6px;margin-bottom:6px;border-radius:2px">';
    html += renderDict('Source SSI', stats.src_ssis);
    html += renderDict('Dest SSI', stats.dst_ssis);
    html += renderDict('Message types', stats.msg_types);
    html += renderDict('MNI', stats.mni_seen);
    html += '</div>';
    html += '<div style="color:#789;font-size:0.85em;margin-bottom:4px">Live PDU stream (najnowsze pierwsze, max 200):</div>';
    pdus.slice(0, 50).forEach(function(p, i){
        var bothOk = p.both_ok ? '<span style="color:#51cf66">✓both</span>' : '<span style="color:#fa5">S only</span>';
        html += '<div style="padding:2px 4px;border-bottom:1px solid #122">';
        html += '<span style="color:#789">' + (p.ts || '?') + ' #' + p.burst_idx + '</span> ' + bothOk + '<br>';
        html += '<span style="color:#cde">' + (p.summary || '').replace(/</g, '&lt;') + '</span>';
        html += '</div>';
    });
    list.innerHTML = html;
    // Re-bind demo button
    var demoBtn = list.querySelector('.ttt-dmo-load');
    var self = this;
    if (demoBtn) demoBtn.addEventListener('click', function(){ self._loadDmoJson(); });
};

TetraMetaPanel.prototype._renderDmoData = function(data, srcPath) {
    var list = this._tttWin && this._tttWin.querySelector('.ttt-dmo-list');
    if (!list) return;
    var meta = data.meta || {};
    var stats = data.stats || {};
    var pdus = data.pdus || [];
    this._tttWin.querySelector('.ttt-dmo-count').textContent = '(' + pdus.length + ')';

    var html = '';
    html += '<div style="color:#789;font-size:0.85em;margin-bottom:6px">źródło: <code>' + srcPath + '</code></div>';
    html += '<div style="background:#021;border:1px solid #234;padding:4px 6px;margin-bottom:6px;border-radius:2px">';
    html += '<div><b style="color:#51cf66">Statystyki dekodowania:</b></div>';
    html += '<div>total burstów: <b>' + meta.total_bursts + '</b></div>';
    html += '<div>SCH/S CRC OK: <b>' + meta.sch_s_ok + '</b>  SCH/H CRC OK: <b>' + meta.sch_h_ok + '</b>  both OK: <b>' + meta.both_ok + '</b></div>';
    html += '</div>';

    var renderDict = function(title, dict) {
        if (!dict || !Object.keys(dict).length) return '';
        var lines = Object.keys(dict).map(function(k){ return k + ' (' + dict[k] + '×)'; });
        return '<div><span style="color:#789">' + title + ':</span> ' + lines.join(', ') + '</div>';
    };
    html += '<div style="background:#021;border:1px solid #234;padding:4px 6px;margin-bottom:6px;border-radius:2px">';
    html += renderDict('Source SSI', stats.unique_src_ssi);
    html += renderDict('Dest SSI', stats.unique_dst_ssi);
    html += renderDict('Message types', stats.message_types);
    html += renderDict('MNI', stats.mni_seen);
    html += '</div>';

    html += '<div style="color:#789;font-size:0.85em;margin-bottom:4px">Lista sparsowanych PDU (' + pdus.length + '):</div>';
    pdus.forEach(function(p, i){
        var bothOk = p.both_ok ? '<span style="color:#51cf66">✓both</span>' : '<span style="color:#fa5">S only</span>';
        html += '<div style="padding:2px 4px;border-bottom:1px solid #122">';
        html += '<span style="color:#789">#' + i + ' burst=' + p.burst_idx + '</span> ' + bothOk + '<br>';
        html += '<span style="color:#cde">' + (p.summary || '').replace(/</g, '&lt;') + '</span>';
        html += '</div>';
    });
    list.innerHTML = html;
};

TetraMetaPanel.prototype._exportCsv = function() {
    var tab = this._tttWin && this._tttWin.querySelector('.ttt-tab[data-active="1"]');
    var name = tab ? tab.getAttribute('data-tab') : 'activity';
    var log = name === 'msreg' ? this._msRegLog : name === 'sds' ? this._sdsLog : this._activityLog;
    if (!log.length) { alert('Pusta lista'); return; }
    // Strip HTML; reverse to chrono order
    var rows = log.slice().reverse().map(function(html){
        var tmp = document.createElement('div'); tmp.innerHTML = html;
        return '"' + (tmp.textContent || '').replace(/"/g, '""') + '"';
    });
    var header = 'timestamp;event\n';
    var csv = header + rows.join('\n');
    var blob = new Blob([csv], {type: 'text/csv;charset=utf-8'});
    var ts = new Date().toISOString().replace(/[:.]/g, '-');
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'tetra_' + name + '_' + ts + '.csv';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function(){ URL.revokeObjectURL(a.href); }, 1000);
};

TetraMetaPanel.prototype._toggleCompact = function() {
    if (!this._tttWin) return;
    this._compact = !this._compact;
    var right = this._tttWin.querySelector('.ttt-right');
    var np = this._tttWin.querySelector('.ttt-nowplaying');
    right.style.display = this._compact ? 'none' : '';
    np.style.display = this._compact ? 'block' : 'block';
    this._tttWin.style.width = this._compact ? '270px' : '680px';
};

TetraMetaPanel.prototype._showRemoteWindow = function() {
    if (this._remoteWin && document.body.contains(this._remoteWin)) {
        this._remoteWin.style.display = 'block';
        this._renderRemote();
        return;
    }
    var self = this;
    var win = document.createElement('div');
    win.className = 'tetra-remote-window';
    win.style.cssText = 'position:fixed;left:100px;top:100px;width:480px;background:#cce0c8;color:#000;'+
        'border:2px solid #345;border-radius:5px;z-index:10001;font-family:system-ui,sans-serif;'+
        'box-shadow:0 4px 14px rgba(0,0,0,0.6);user-select:none';
    win.innerHTML = ''+
        '<div class="rem-title" style="background:#1a3050;color:#fff;padding:3px 8px;cursor:move;display:flex;justify-content:space-between;align-items:center;border-radius:3px 3px 0 0">'+
        '  <b>Remote</b>'+
        '  <span class="rem-close" style="cursor:pointer;color:#fcc;font-weight:bold">✕</span>'+
        '</div>'+
        '<div style="padding:8px">'+
        '  <div style="font-size:0.9em;color:#345">Group <span class="rem-duration" style="float:right;font-family:monospace;color:#345"></span></div>'+
        '  <div class="rem-group" style="font-size:3em;font-weight:bold;line-height:1.0;min-height:50px;color:#000">—</div>'+
        '  <div class="rem-group-sub" style="font-size:0.8em;color:#345">&nbsp;</div>'+
        '  <div style="margin-top:8px;font-size:0.9em;color:#345">User</div>'+
        '  <div class="rem-user" style="font-size:3em;font-weight:bold;line-height:1.0;min-height:50px;color:#000">—</div>'+
        '  <div class="rem-user-sub" style="font-size:0.8em;color:#345">&nbsp;</div>'+
        '</div>';
    document.body.appendChild(win);
    this._remoteWin = win;
    win.querySelector('.rem-close').addEventListener('click', function(){ win.style.display = 'none'; });
    var title = win.querySelector('.rem-title'); var drag = null;
    title.addEventListener('mousedown', function(e){
        if (e.target.classList.contains('rem-close')) return;
        drag = { ox: e.clientX - win.offsetLeft, oy: e.clientY - win.offsetTop };
        e.preventDefault();
    });
    document.addEventListener('mousemove', function(e){
        if (!drag) return;
        win.style.left = (e.clientX - drag.ox) + 'px';
        win.style.top = (e.clientY - drag.oy) + 'px';
    });
    document.addEventListener('mouseup', function(){ drag = null; });
    this._renderRemote();
};

TetraMetaPanel.prototype._renderRemote = function() {
    if (!this._remoteWin || this._remoteWin.style.display === 'none') return;
    var w = this._remoteWin, c = this._currentCall;
    var gLbl = c && c.gssi ? this._labelFor('gssi', c.gssi) : '';
    var tx = c ? c.tx_ssi : null;
    var uLbl = tx ? this._labelFor('issi', tx) : '';
    w.querySelector('.rem-group').textContent = gLbl || (c && c.gssi ? c.gssi : '—');
    w.querySelector('.rem-group-sub').textContent = c && c.gssi && gLbl ? ('GSSI: ' + c.gssi) : ' ';
    w.querySelector('.rem-user').textContent = uLbl || tx || '—';
    w.querySelector('.rem-user-sub').textContent = tx && uLbl ? ('ISSI: ' + tx) : ' ';
    w.querySelector('.rem-duration').textContent = c ? this._durationStr(c.started_at) : '';
};

TetraMetaPanel.prototype._actionLabel = function(action) {
    var map = {
        'location_update_accept': 'LU Accept',
        'location_update_reject': 'LU Reject',
        'location_update_command': 'LU Command',
        'location_update_proceeding': 'LU Proceeding',
        'group_attach': 'Group Attach',
        'group_detach': 'Group Detach',
        'attach_detach_ack': 'Attach/Detach ACK',
        'authentication_demand': 'Auth Demand',
        'authentication_result': 'Auth Result',
        'otar': 'OTAR — Key Update',
        'ck_change_demand': 'CK Change Demand',
        'ms_disable': '🚫 MS DISABLED',
        'ms_enable': '✓ MS Enabled',
        'mm_status': 'MM Status'
    };
    return map[action] || action || '?';
};

TetraMetaPanel.prototype._actionColor = function(action) {
    if (action === 'location_update_reject') return '#ff8787';
    if (action === 'group_detach') return '#ffd43b';
    if (action === 'authentication_demand') return '#9af';
    if (action === 'authentication_result') return '#cde';
    if (action === 'group_attach') return '#51cf66';
    if (action === 'otar' || action === 'ck_change_demand') return '#fc9';
    if (action === 'ms_disable') return '#ff8787';
    if (action === 'ms_enable') return '#51cf66';
    return '#cde';
};

// MmStatusDownlink subset (most-likely relevant values)
TetraMetaPanel.prototype._MM_STATUS_DL = {
    0: 'Reserved',
    1: 'Change of energy saving mode request',
    2: 'Change of energy saving mode response',
    3: 'Dual watch mode request',
    4: 'Terminating dual watch mode request',
    5: 'Change of dual watch mode response',
    6: 'Start of direct mode operation',
    7: 'MS frequency bands information',
    16: 'Acceptance to start DM gateway operation',
    17: 'Rejection to start DM gateway operation',
    18: 'Acceptance to continue DM gateway operation',
    19: 'Rejection to continue DM gateway operation',
    20: 'Acceptance to stop DM gateway operation',
    21: 'Acceptance of DM-MS addresses',
    22: 'Command to remove DM-MS addresses',
    23: 'Command to change registration label',
    24: 'Command to stop DM gateway operation'
};

TetraMetaPanel.prototype._correlateTsi = function(windowMs) {
    // Find SSIs seen recently (within windowMs) — candidates for the TSI-form LU
    var now = Date.now();
    var w = windowMs || 5000;
    var cands = [];
    for (var ssi in this._ssiSeenAt) {
        var dt = now - this._ssiSeenAt[ssi];
        if (dt <= w) cands.push({ ssi: ssi, dt: dt });
    }
    cands.sort(function(a,b){ return a.dt - b.dt; });
    return cands.slice(0, 3);
};

TetraMetaPanel.prototype._addMsRegEvent = function(data) {
    var action = data.action || '?';
    if (!this._filterAllows(action)) return;
    // Update terminal DB
    if (data.ssi) this._touchTerminal(data.ssi, 'ms_register', {
        action: action, la: data.la, subscr_class: data.subscr_class, gssi: data.gssi
    });
    var ts = this._timestamp();
    var color = this._actionColor(action);
    var laTag = data.la ? '[LA: ' + data.la + '] ' : '';
    var summary = data.summary || this._actionLabel(action);
    // TSI form (addr_type=2) → SSI field is 0; correlate from recent active_ssi
    var tsiHint = '';
    if (data.addr_type === 2 && (!data.ssi || data.ssi === 0)) {
        var cands = this._correlateTsi(5000);
        if (cands.length) {
            tsiHint = ' · TSI-form, likely SSI: ' + cands.map(function(c){
                return c.ssi + ' (Δ' + (c.dt/1000).toFixed(1) + 's)';
            }).join(', ');
        }
    }
    // Append GSSI/ISSI labels if known
    var lblExtras = [];
    if (data.ssi) {
        var sl = this._labelFor('issi', data.ssi);
        if (sl) lblExtras.push('ISSI:[' + sl + ']');
    }
    if (data.gssi) {
        var gl = this._labelFor('gssi', data.gssi);
        if (gl) lblExtras.push('GSSI:[' + gl + ']');
    }
    var extra = lblExtras.length ? ' · ' + lblExtras.join(' ') : '';
    if (data.subscr_class != null) extra += ' · class:0x' + data.subscr_class.toString(16);
    if (data.mm_status_code != null) {
        var mn = this._MM_STATUS_DL[data.mm_status_code];
        extra += ' · code:' + data.mm_status_code + (mn ? ' (' + mn + ')' : '');
    }
    var termSummary = data.ssi ? this._renderTerminalSummary(data.ssi) : '';
    var row = '<div style="color:' + color + '">' +
              '<span style="color:#789">' + ts + '</span> ' +
              '<span style="color:#9ab">' + laTag + '</span>' +
              summary + extra + (tsiHint ? '<span style="color:#9cf">' + tsiHint + '</span>' : '') +
              termSummary +
              '</div>';
    this._msRegLog.unshift(row);
    if (this._msRegLog.length > 300) this._msRegLog.length = 300;
    if (this._tttWin && this._tttWin.style.display !== 'none') {
        this._tttWin.querySelector('.ttt-msreg-list').innerHTML = this._msRegLog.join('');
        this._tttWin.querySelector('.ttt-msreg-count').textContent = '(' + this._msRegLog.length + ')';
    }
    this._stashCurrent();
};

TetraMetaPanel.prototype.getCallTypeLabel = function(callType) {
    if (!callType) return '';
    for (var key in this.callTypeNames) {
        if (callType.indexOf(key) === 0) {
            var suffix = callType.substring(key.length);
            return this.callTypeNames[key] + suffix;
        }
    }
    return callType;
};

TetraMetaPanel.prototype.update = function(data) {
    if (!this.isSupported(data)) return;
    var el = $(this.el);
    var type = data.type;

    if (type === 'session_reset') {
        // Backend detected network change (MCC/MNC) — reset all per-session caches
        this._seenSsis = {};
        this._ssiSeenAt = {};
        this._activeSsiPrev = {};
        this._terminalDb = {};
        this._currentCall = null;
        this._stopDurationTimer();
        var $el = $(this.el);
        this._logActivity('<i>--- session reset (' + (data.old_network||'?') + ' → ' + (data.new_network||'?') + ') ---</i>', '#9ab');
        return;
    }
    if (type === 'netinfo') {
        // Build key from MCC/MNC/DL freq for true cell-level partitioning
        if (data.dl_freq && data.mcc && data.mnc) {
            this._switchFreq(data.mcc + '-' + data.mnc + '-' + data.dl_freq);
        }
        var mcc = data.mcc || '---';
        var mnc = data.mnc || '---';
        var key = mcc + '-' + mnc;
        var networkName = this.networkNames[key] || key;

        el.find('.tetra-network').text(networkName);
        el.find('.tetra-mcc').text(mcc);
        el.find('.tetra-mnc').text(mnc);

        if (data.dl_freq) {
            el.find('.tetra-dl-freq').text((data.dl_freq / 1e6).toFixed(4) + ' MHz');
        }
        if (data.ul_freq) {
            el.find('.tetra-ul-freq').text((data.ul_freq / 1e6).toFixed(4) + ' MHz');
        }
        if (data.color_code !== undefined) {
            el.find('.tetra-color-code').text(data.color_code);
        }
        if (data.la) {
            el.find('.tetra-la').text(data.la);
        }
        if (data.dl_freq) {
            this._lastCarrier = (data.dl_freq / 1e6).toFixed(4) + ' MHz';
        }
        this._lastNet = key + (networkName !== key ? ' ' + networkName : '');
        this._renderTttWindow();
        el.find('.tetra-encrypted').text(data.encrypted ? 'TAK' : 'NIE')
            .css('color', data.encrypted ? '#ff6b6b' : '#51cf66');
        if (data.tetra_time) {
            el.find('.tetra-tetra-time').text(this._formatTetraTime(data.tetra_time));
        }
    }
    else if (type === 'dmo_burst') {
        // Live DMO burst (DMAC-SYNC / DPRES-SYNC) z tetra_dmo_decoder.py
        if (!this._dmoLive) this._dmoLive = { pdus: [], stats: { sch_s_ok: 0, sch_h_ok: 0, n_pdus: 0,
            src_ssis: {}, dst_ssis: {}, msg_types: {}, mni_seen: {} } };
        var dl = this._dmoLive;
        dl.stats.n_pdus++;
        if (data.src !== null && data.src !== undefined) {
            var k = String(data.src);
            dl.stats.src_ssis[k] = (dl.stats.src_ssis[k] || 0) + 1;
        }
        if (data.dst !== null && data.dst !== undefined) {
            var k2 = String(data.dst);
            dl.stats.dst_ssis[k2] = (dl.stats.dst_ssis[k2] || 0) + 1;
        }
        if (data.msg_type) {
            dl.stats.msg_types[data.msg_type] = (dl.stats.msg_types[data.msg_type] || 0) + 1;
        }
        if (data.mcc !== null && data.mnc !== null && data.mcc !== undefined) {
            var mk = data.mcc + '-' + data.mnc;
            dl.stats.mni_seen[mk] = (dl.stats.mni_seen[mk] || 0) + 1;
        }
        dl.pdus.unshift({
            burst_idx: dl.stats.n_pdus,
            both_ok: data.sync_type === 'DMAC-SYNC' && data.msg_type ? true : (data.msg_type !== ''),
            summary: data.summary || '',
            msg: data.msg_type || '',
            src: data.src, dst: data.dst,
            mcc: data.mcc, mnc: data.mnc,
            tn: data.tn, fn: data.fn,
            ts: this._timestamp()
        });
        if (dl.pdus.length > 200) dl.pdus.length = 200;
        // Update counter
        if (this._tttWin) {
            var cntEl = this._tttWin.querySelector('.ttt-dmo-count');
            if (cntEl) cntEl.textContent = '(' + dl.stats.n_pdus + ')';
            // Auto-refresh DMO tab jeśli aktywny
            var dmoTab = this._tttWin.querySelector('.ttt-tab[data-tab="dmo"]');
            if (dmoTab && dmoTab.getAttribute('data-active') === '1') {
                this._renderDmoLive();
            }
        }
        return;
    }
    else if (type === 'dmo_stats') {
        // Periodic DMO summary z decoder
        if (!this._dmoLive) this._dmoLive = { pdus: [], stats: {} };
        this._dmoLive.lastStats = data;
        return;
    }
    else if (type === 'encinfo') {
        el.find('.tetra-encrypted').text(data.encrypted ? 'TAK (' + data.enc_mode + ')' : 'NIE')
            .css('color', data.encrypted ? '#ff6b6b' : '#51cf66');
    }
    else if (type === 'call_setup') {
        // Lockout / Hold gating
        if (this._isLockedOut(data.ssi) || this._isHoldFiltered(data.ssi)) return;
        if (!this._filterAllows('call_setup')) return;
        if (data.calling_ssi) this._touchTerminal(data.calling_ssi, 'call_setup', { gssi: data.ssi, call_id: data.call_id });
        if (data.ssi2) this._touchTerminal(data.ssi2, 'call_setup', { gssi: data.ssi, call_id: data.call_id });
        var ctLabel = this.getCallTypeLabel(data.call_type);
        el.find('.tetra-call-status').text('Zestawienie').css('color', '#ffd43b');
        el.find('.tetra-call-type').text(ctLabel ? '[' + ctLabel + ']' : '');
        el.find('.tetra-gssi').text(data.ssi || '---');
        var issi = data.ssi2 || data.calling_ssi || '---';
        el.find('.tetra-issi').text(issi);
        el.find('.tetra-call-id').text('CID:' + (data.call_id || ''));
        // Update floating TTT window state
        if (!this._currentCall || this._currentCall.call_id !== data.call_id) {
            this._currentCall = {
                call_id: data.call_id, gssi: data.ssi, issis: new Set(),
                tx_ssi: null, timeslot: this._currentTimeslot, last_update: this._timestamp(),
                status: 'Zestawienie', call_type: ctLabel, started_at: Date.now()
            };
            this._startDurationTimer();
            this._playSound('setup');
            var gname = this._labelFor('gssi', data.ssi);
            var gnTag = gname ? ' [' + gname + ']' : '';
            this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — Setup — Group: ' +
                (data.ssi || '?') + gnTag + (ctLabel ? ' [' + ctLabel + ']' : ''), '#ffd43b');
        }
        var c = this._currentCall;
        if (data.calling_ssi && !c.issis.has(data.calling_ssi)) {
            c.issis.add(data.calling_ssi);
            this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — Calling SSI: ' + data.calling_ssi, '#9af');
        }
        if (data.ssi2 && !c.issis.has(data.ssi2)) c.issis.add(data.ssi2);
        c.last_update = this._timestamp();
        this._renderTttWindow();
    }
    else if (type === 'call_connect') {
        if (this._isLockedOut(data.ssi) || this._isHoldFiltered(data.ssi)) return;
        if (!this._filterAllows('call_connect')) return;
        if (data.calling_ssi) this._touchTerminal(data.calling_ssi, 'call_connect', { gssi: data.ssi, call_id: data.call_id });
        if (data.ssi2) this._touchTerminal(data.ssi2, 'call_connect', { gssi: data.ssi, call_id: data.call_id });
        el.find('.tetra-call-status').text('Aktywne').css('color', '#51cf66');
        if (data.ssi) el.find('.tetra-gssi').text(data.ssi);
        var issi = data.ssi2 || data.calling_ssi;
        if (issi) el.find('.tetra-issi').text(issi);
        if (!this._currentCall) {
            this._currentCall = { call_id: data.call_id, gssi: data.ssi, issis: new Set(),
                tx_ssi: null, timeslot: this._currentTimeslot, last_update: this._timestamp(), status: 'Aktywne', started_at: Date.now() };
            this._startDurationTimer();
        } else {
            this._currentCall.status = 'Aktywne';
            this._currentCall.last_update = this._timestamp();
        }
        if (issi) this._currentCall.issis.add(issi);
        this._renderTttWindow();
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — Connected' +
            (issi ? ' — SSI: ' + issi : ''), '#51cf66');
    }
    else if (type === 'tx_grant') {
        if (this._isLockedOut(data.ssi) || this._isHoldFiltered(data.ssi)) return;
        if (!this._filterAllows('tx_grant')) return;
        if (data.calling_ssi) this._touchTerminal(data.calling_ssi, 'tx_grant', { gssi: data.ssi, call_id: data.call_id, tx: true });
        if (data.ssi2) this._touchTerminal(data.ssi2, 'tx_grant', { gssi: data.ssi, call_id: data.call_id, tx: true });
        el.find('.tetra-call-status').text('TX').css('color', '#51cf66');
        if (data.ssi) el.find('.tetra-gssi').text(data.ssi);
        var issi = data.ssi2 || data.calling_ssi;
        if (issi) el.find('.tetra-issi').text(issi);
        if (!this._currentCall || this._currentCall.call_id !== data.call_id) {
            this._currentCall = { call_id: data.call_id, gssi: data.ssi, issis: new Set(),
                tx_ssi: null, timeslot: this._currentTimeslot, last_update: this._timestamp(), status: 'TX', started_at: Date.now() };
            this._startDurationTimer();
        }
        var c = this._currentCall;
        var prevTx = c.tx_ssi;
        c.status = 'TX';
        c.last_update = this._timestamp();
        if (issi) { c.issis.add(issi); c.tx_ssi = issi; }
        if (c.tx_ssi !== prevTx) this._playSound('tx');
        this._renderTttWindow();
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — D-TX-Granted SSI: ' + (issi || '?'), '#51cf66');
    }
    else if (type === 'call_release') {
        if (!this._filterAllows('call_release')) {
            this._currentCall = null; this._stopDurationTimer(); this._renderTttWindow();
            el.find('.tetra-call-status').text('Idle').css('color', '#868e96'); return;
        }
        el.find('.tetra-call-status').text('Idle').css('color', '#868e96');
        el.find('.tetra-call-type').text('');
        el.find('.tetra-gssi').text('---');
        el.find('.tetra-issi').text('---');
        el.find('.tetra-call-id').text('');
        var reasonRaw = data.reason;
        var reasonText = '';
        if (reasonRaw != null && reasonRaw !== '') {
            var resolved = this._lookupDisconnectionCause(reasonRaw);
            reasonText = ' (' + reasonRaw + (resolved ? ' — ' + resolved : '') + ')';
        }
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — D-Released' + reasonText, '#ff8787');
        this._currentCall = null;
        this._stopDurationTimer();
        this._playSound('release');
        this._renderTttWindow();
    }
    else if (type === 'status') {
        el.find('.tetra-call-status').text('Status: ' + data.status).css('color', '#4dabf7');
        el.find('.tetra-gssi').text(data.ssi || '---');
        el.find('.tetra-issi').text(data.ssi2 || '---');
    }
    else if (type === 'resource') {
        // SSI2 in resource = ISSI of individual subscriber (if available)
        if (data.ssi2) {
            el.find('.tetra-issi').text(data.ssi2);
        }
    }
    else if (type === 'ms_register') {
        this._addMsRegEvent(data);
    }
    else if (type === 'sds') {
        if (!this._filterAllows('sds')) return;
        this._addSdsEvent(data);
    }
    else if (type === 'call_disconnect') {
        if (!this._filterAllows('call_disconnect')) return;
        var dc = this._lookupDisconnectionCause(data.disconnect_cause);
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — D-Disconnect (' + data.disconnect_cause + (dc ? ' — ' + dc : '') + ')', '#ff8787');
    }
    else if (type === 'call_alert') {
        if (!this._filterAllows('call_alert')) return;
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — D-Alert (dzwoni)', '#ffd43b');
    }
    else if (type === 'call_proceeding') {
        if (!this._filterAllows('call_proceeding')) return;
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — D-Call-Proceeding', '#9cf');
    }
    else if (type === 'connect_ack') {
        if (!this._filterAllows('connect_ack')) return;
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — D-Connect-ACK (NID:' + (data.nid || '?') + ')', '#51cf66');
    }
    else if (type === 'call_info') {
        if (!this._filterAllows('call_info')) return;
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — D-Info', '#cde');
    }
    else if (type === 'call_restore') {
        if (!this._filterAllows('call_restore')) return;
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — D-Call-Restore (handover)', '#9cf');
    }
    else if (type === 'tx_state') {
        var st = data.subtype || '?';
        if (!this._filterAllows('tx_' + st)) return;
        var stColor = st === 'ceased' ? '#fc9' : st === 'interrupt' ? '#ff8787' : st === 'wait' ? '#ffd43b' : '#9cf';
        this._logActivity('<b>Call ID: ' + (data.call_id || '?') + '</b> — TX ' + st.toUpperCase(), stColor);
    }
    else if (type === 'facility') {
        if (!this._filterAllows('facility')) return;
        if (data.ssi) this._touchTerminal(data.ssi, 'facility');
        var fl = data.ssi ? this._labelFor('issi', data.ssi) : '';
        this._logActivity('D-Facility SSI: ' + (data.ssi || '?') + (fl ? ' [' + fl + ']' : '') + ' IDX:' + (data.idx || '?'), '#9cf');
    }
    else if (type === 'cell_change') {
        if (!this._filterAllows('cell_change_' + (data.action || ''))) return;
        var actMap = {
            new_cell: 'D-New-Cell (handover)',
            prepare_fail: 'D-Prepare-Fail',
            restore_ack: 'D-Restore-ACK',
            restore_fail: 'D-Restore-Fail',
            channel_response: 'D-Channel-Response',
            nwrk_broadcast_ext: 'D-Nwrk-Broadcast-Ext'
        };
        var col = data.action === 'prepare_fail' || data.action === 'restore_fail' ? '#ff8787' : '#9cf';
        this._logActivity(actMap[data.action] || data.action || '?', col);
    }
    else if (type === 'neighbours') {
        var cells = data.cells || [];
        el.find('.tetra-neighbour-count').text(cells.length);
        if (cells.length === 0) {
            el.find('.tetra-neighbour-list').text('');
        } else {
            var max = 4;
            var parts = cells.slice(0, max).map(function(c){
                return 'c' + c.cell_id + '@' + (c.dlf / 1e6).toFixed(3);
            });
            var extra = cells.length > max ? ' +' + (cells.length - max) : '';
            el.find('.tetra-neighbour-list').text('[' + parts.join(', ') + extra + ']');
        }
        if (data.tetra_time) {
            el.find('.tetra-tetra-time').text(this._formatTetraTime(data.tetra_time));
        }
    }
    else if (type === 'active_ssi') {
        var ssis = data.ssis || [];
        // Log every NEW SSI seen in this session to Activity feed (with classification)
        var currentSet = {};
        for (var i = 0; i < ssis.length; i++) {
            var r = ssis[i];
            currentSet[r.ssi] = true;
            this._touchTerminal(r.ssi, 'active_ssi', { encr: r.encr });
            if (!this._seenSsis[r.ssi]) {
                this._seenSsis[r.ssi] = true;
                this._ssiSeenAt[r.ssi] = Date.now();
                if (!this._filterAllows('ssi_appeared')) continue;
                var lblNew = this._labelFor('issi', r.ssi);
                var lblTag = lblNew ? ' [' + lblNew + ']' : '';
                var kind, color;
                if (r.encr === 2) {
                    kind = 'ESI alias 🔒'; color = '#ffd43b';
                } else if (r.confirmed) {
                    kind = 'Real ISSI'; color = '#51cf66';
                } else {
                    kind = 'Adres SSI (GSSI/USSI)'; color = '#9af';
                }
                this._logActivity(kind + ' ' + r.ssi + lblTag + ' — pojawił się w komórce', color);
            }
        }
        if (this._filterAllows('ssi_disappeared')) {
            for (var prev in this._activeSsiPrev) {
                if (!currentSet[prev]) {
                    var lblGone = this._labelFor('issi', prev);
                    this._logActivity('SSI ' + prev + (lblGone ? ' [' + lblGone + ']' : '') + ' — zniknął z komórki', '#ffa5a5');
                }
            }
        }
        this._activeSsiPrev = currentSet;
    }
    else if (type === 'sync_stat') {
        // TETRA sync training-sequence (y, 38b) detection rate from tetra_demod.
        // Same pattern is used in TMO downlink SB and DMO DSB; rate context
        // alone hints at presence but does not yet disambiguate. Stored on
        // the panel so the DMO badge can light up when there is sync activity
        // but no netinfo (= almost certainly not TMO downlink we're decoding).
        this._lastSyncRate = data.hits_per_s || 0;
        this._lastSyncTs = Date.now();
        var rate = this._lastSyncRate;
        var hasNetinfo = !!(this._currentCall || (el.find('.tetra-mcc').text() !== '---'));
        var dmoHint = (rate > 0.3 && !hasNetinfo);
        var badge = el.find('.tetra-dmo-badge');
        if (badge.length === 0) {
            el.find('.tetra-burst-rate').after(
                '<span class="tetra-dmo-badge" style="margin-left:8px;font-size:0.85em;padding:1px 5px;border-radius:3px;display:none" title="Wykryto sync TETRA bez kontekstu TMO — możliwy DMO"></span>'
            );
            badge = el.find('.tetra-dmo-badge');
        }
        if (dmoHint) {
            badge.text('DMO? ' + rate.toFixed(1) + '/s')
                 .css({ background: '#9c36b5', color: '#fff' })
                 .show();
        } else if (rate > 0.3) {
            badge.text('sync ' + rate.toFixed(1) + '/s')
                 .css({ background: '#212529', color: '#adb5bd' })
                 .show();
        } else {
            badge.hide();
        }
    }
    else if (type === 'burst') {
        // AFC
        if (data.afc !== undefined) {
            var afcHz = data.afc;
            var afcColor = Math.abs(afcHz) < 500 ? '#51cf66' : (Math.abs(afcHz) < 1500 ? '#ffd43b' : '#ff6b6b');
            el.find('.tetra-afc').text(afcHz.toFixed(0) + ' Hz').css('color', afcColor);
        }
        // Burst rate
        if (data.burst_rate !== undefined) {
            var br = data.burst_rate;
            var brColor = br > 40 ? '#51cf66' : (br > 20 ? '#ffd43b' : '#ff6b6b');
            el.find('.tetra-burst-rate').text(br.toFixed(0) + '/s').css('color', brColor);
        }
        // Timeslots — fine-grained DL_USAGE with TTL-based aging
        if (data.timeslots) {
            var TS_STYLE = {
                traffic:        { bg: '#e67700', fg: '#fff', letter: 'T',  label: 'Traffic (aktywna rozmowa)' },
                control:        { bg: '#1971c2', fg: '#fff', letter: 'C',  label: 'Assigned control (MCCH — sygnalizacja)' },
                common_control: { bg: '#0c8599', fg: '#fff', letter: 'Cc', label: 'Common control (SCCH)' },
                reserved:       { bg: '#7048e8', fg: '#fff', letter: 'R',  label: 'Reserved' },
                unallocated:    { bg: '#2b8a3e', fg: '#fff', letter: '·',  label: 'Unallocated (slot wolny)' },
                stale:          { bg: '#343a40', fg: '#888', letter: '⌛', label: 'Stale (brak ACCESS-ASSIGN >2 s)' },
                unknown:        { bg: '#212529', fg: '#666', letter: '?',  label: 'Brak danych' },
                assigned:       { bg: '#e67700', fg: '#fff', letter: 'T',  label: 'Assigned (legacy)' }
            };
            var assignedTs = null;
            for (var tn in data.timeslots) {
                var entry = data.timeslots[tn];
                var usage, age = null;
                if (typeof entry === 'string') { usage = entry; }
                else { usage = entry.usage; age = entry.age; }
                var style = TS_STYLE[usage] || TS_STYLE.unknown;
                var tsEl = el.find('.tetra-ts-' + tn);
                tsEl.removeClass('busy idle');
                tsEl.css({
                    background: style.bg,
                    color: style.fg,
                    'min-width': '22px',
                    'text-align': 'center',
                    'border-radius': '3px',
                    'padding': '1px 4px',
                    'margin-right': '3px',
                    'font-family': 'monospace'
                });
                tsEl.html(tn + '<sub style="font-size:0.75em;opacity:0.85;margin-left:2px">' + style.letter + '</sub>');
                var ageStr = (age != null) ? (' · ' + age.toFixed(1) + 's temu') : '';
                tsEl.attr('title', 'TS' + tn + ': ' + style.label + ageStr);
                if (usage === 'traffic' && assignedTs == null) assignedTs = tn;
            }
            if (assignedTs != null) {
                this._currentTimeslot = assignedTs;
                if (this._currentCall) {
                    this._currentCall.timeslot = assignedTs;
                    el.find('.tetra-timeslot').text(assignedTs);
                }
            }
        }
        // Call type from burst (updated periodically)
        if (data.call_type) {
            var ct = this.getCallTypeLabel(data.call_type);
            if (ct && el.find('.tetra-call-status').text() !== 'Idle') {
                el.find('.tetra-call-type').text('[' + ct + ']');
            }
        }
    }
};

TetraMetaPanel.prototype.clear = function() {
    MetaPanel.prototype.clear.call(this);
    var el = $(this.el);
    el.find('.tetra-network, .tetra-mcc, .tetra-mnc').text('---');
    el.find('.tetra-dl-freq, .tetra-ul-freq, .tetra-carrier').text('---');
    el.find('.tetra-color-code, .tetra-la').text('---');
    el.find('.tetra-encrypted').text('---').css('color', '');
    el.find('.tetra-afc, .tetra-burst-rate').text('---').css('color', '');
    el.find('.tetra-ts').removeClass('busy idle').each(function(i){
        var n = i + 1;
        $(this).html(n + '<sub style="font-size:0.75em;opacity:0.85;margin-left:2px">?</sub>')
               .css({ background: '#212529', color: '#666' })
               .attr('title', 'TS' + n + ': brak danych');
    });
    el.find('.tetra-neighbour-count').text('0');
    el.find('.tetra-neighbour-list').text('');
    this._activityLog = [];
    this._msRegLog = [];
    this._sdsLog = [];
    this._seenSsis = {};
    this._stopDurationTimer();
    this._currentCall = null;
    this._currentTimeslot = null;
    this._renderTttWindow();
    el.find('.tetra-activity-list').html('').hide();
    el.find('.tetra-activity-arrow').text('▸');
    el.find('.tetra-activity-count').text('0');
    el.find('.tetra-activity-clear').hide();
};

// DmrEpMetaPanel — panel OpenWebRX+ pokazujacy zdekodowane/odszyfrowane dane DMR EP.
// Author: SP8MB
// Wstrzykiwany do lib/MetaPanel.js przez patch_dmr_ep_panel.py; rejestrowany jako
// MetaPanel.types["dmrep"] i pokazywany dla trybu "dmr-ep" (DemodulatorPanel mapuje
// dmr-ep -> dmrep). Odbiera meta protocol="DMR" (isSupported) i loguje przewijana liste.
function DmrEpMetaPanel(el) {
    MetaPanel.call(this, el);
    this.modes = ['DMR'];
    this.count = 0;
    this._lastKey = null;
    var $el = $(el), self = this;
    $el.on('click', '.openwebrx-dmrep-clear', function () {
        $el.find('.openwebrx-dmrep-list').empty();
        self.count = 0;
        $el.find('.openwebrx-dmrep-count').text('0');
    });
}

DmrEpMetaPanel.prototype = new MetaPanel();

DmrEpMetaPanel.prototype._time = function () {
    var d = new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
};

DmrEpMetaPanel.prototype.update = function (data) {
    if (!this.isSupported(data)) return;
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
    var rows = list.children();
    if (rows.length > 200) rows.slice(200).remove();
    this.count++;
    $(this.el).find('.openwebrx-dmrep-count').text(this.count);
};

// nie czyscimy logu przy zmianie trybu — zostaw historie
DmrEpMetaPanel.prototype.clear = function () { };

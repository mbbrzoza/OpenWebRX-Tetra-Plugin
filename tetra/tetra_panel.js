// TETRA meta panel for OpenWebRX+
// Author: SP8MB

function TetraMetaPanel(el) {
    MetaPanel.call(this, el);
    this.modes = ['TETRA'];
    this.networkNames = {
        '901-9999': 'SR8LST'
    };
    this.callTypeNames = {
        'individual': 'Indyw.',
        'group': 'Grupowe',
        'broadcast': 'Broadcast',
        'acknowledged group': 'Grupa potw.',
        'other': 'Inne'
    };
}

TetraMetaPanel.prototype = new MetaPanel();

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

    if (type === 'netinfo') {
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
        el.find('.tetra-encrypted').text(data.encrypted ? 'TAK' : 'NIE')
            .css('color', data.encrypted ? '#ff6b6b' : '#51cf66');
    }
    else if (type === 'encinfo') {
        el.find('.tetra-encrypted').text(data.encrypted ? 'TAK (' + data.enc_mode + ')' : 'NIE')
            .css('color', data.encrypted ? '#ff6b6b' : '#51cf66');
    }
    else if (type === 'call_setup') {
        var ctLabel = this.getCallTypeLabel(data.call_type);
        el.find('.tetra-call-status').text('Zestawienie').css('color', '#ffd43b');
        el.find('.tetra-call-type').text(ctLabel ? '[' + ctLabel + ']' : '');
        el.find('.tetra-gssi').text(data.ssi || '---');
        el.find('.tetra-issi').text(data.ssi2 || '---');
        el.find('.tetra-call-id').text('CID:' + (data.call_id || ''));
    }
    else if (type === 'call_connect') {
        el.find('.tetra-call-status').text('Aktywne').css('color', '#51cf66');
        if (data.ssi) el.find('.tetra-gssi').text(data.ssi);
        if (data.ssi2) el.find('.tetra-issi').text(data.ssi2);
    }
    else if (type === 'tx_grant') {
        el.find('.tetra-call-status').text('TX').css('color', '#51cf66');
        if (data.ssi) el.find('.tetra-gssi').text(data.ssi);
        if (data.ssi2) el.find('.tetra-issi').text(data.ssi2);
    }
    else if (type === 'call_release') {
        el.find('.tetra-call-status').text('Idle').css('color', '#868e96');
        el.find('.tetra-call-type').text('');
        el.find('.tetra-gssi').text('---');
        el.find('.tetra-issi').text('---');
        el.find('.tetra-call-id').text('');
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
        // Timeslots
        if (data.timeslots) {
            el.find('.tetra-ts').removeClass('busy idle');
            for (var tn in data.timeslots) {
                var usage = data.timeslots[tn];
                var tsEl = el.find('.tetra-ts-' + tn);
                if (usage === 'assigned') {
                    tsEl.addClass('busy');
                } else if (usage === 'unallocated') {
                    tsEl.addClass('idle');
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
    el.find('.tetra-dl-freq, .tetra-ul-freq').text('---');
    el.find('.tetra-color-code, .tetra-la').text('---');
    el.find('.tetra-encrypted').text('---').css('color', '');
    el.find('.tetra-afc, .tetra-burst-rate').text('---').css('color', '');
    el.find('.tetra-call-status').text('Idle').css('color', '#868e96');
    el.find('.tetra-call-type').text('');
    el.find('.tetra-gssi, .tetra-issi').text('---');
    el.find('.tetra-call-id').text('');
    el.find('.tetra-ts').removeClass('busy idle');
};

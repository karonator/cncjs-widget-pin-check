(function () {
  'use strict';

  // cncjs passes the session token to the widget iframe as ?token=...
  var token = new URLSearchParams(window.location.search).get('token') || '';
  var PINS = ['X', 'Y', 'Z', 'P'];
  var PIN_NAMES = { X: 'X limit', Y: 'Y limit', Z: 'Z limit', P: 'Probe' };
  var leds = document.querySelectorAll('.led');
  var checks = document.querySelectorAll('.check');
  var segments = document.querySelectorAll('.segments span');
  var hint = document.getElementById('hint');
  var summary = document.getElementById('summary');
  var progress = document.getElementById('progress');

  var state = {
    port: '',
    controllerType: '',
    pinState: '',
    // Latch: a pin counts as checked once it has been seen released and then triggered
    seenReleased: {},
    checked: {}
  };

  function isActive() {
    // Only Grbl reports input pin states (Pn:XYZP...)
    return !!state.port && state.controllerType === 'Grbl';
  }

  function isTriggered(pin) {
    return isActive() && state.pinState.indexOf(pin) >= 0;
  }

  function resetChecks() {
    state.seenReleased = {};
    state.checked = {};
  }

  function updateChecks() {
    if (!isActive()) {
      return;
    }
    PINS.forEach(function (pin) {
      if (!isTriggered(pin)) {
        state.seenReleased[pin] = true;
      } else if (state.seenReleased[pin]) {
        state.checked[pin] = true;
      }
    });
  }

  function render() {
    var active = isActive();

    Array.prototype.forEach.call(leds, function (led) {
      var pin = led.dataset.pin;
      var triggered = isTriggered(pin);
      led.classList.toggle('off', active && !triggered);
      led.classList.toggle('on', triggered);

      // Colour is not enough for everyone: spell the state out for tooltips and screen readers
      var text = PIN_NAMES[pin] + ': ' + (!active ? 'not connected' : triggered ? 'triggered' : 'released');
      if (active && state.checked[pin]) {
        text += ', checked';
      }
      led.title = text;
      led.setAttribute('aria-label', text);
    });
    Array.prototype.forEach.call(checks, function (check) {
      check.hidden = !(active && state.checked[check.dataset.pin]);
    });
    Array.prototype.forEach.call(segments, function (segment) {
      segment.classList.toggle('done', !!state.checked[segment.dataset.pin]);
    });

    var count = PINS.filter(function (pin) {
      return state.checked[pin];
    }).length;
    progress.textContent = count === PINS.length ? '\u2713 All pins OK' : count + ' of ' + PINS.length + ' checked';
    summary.classList.toggle('ok', count === PINS.length);

    hint.hidden = active;
    summary.hidden = !active;
  }

  document.getElementById('reset').addEventListener('click', function () {
    resetChecks();
    updateChecks();
    render();
  });

  // Messages to the cncjs workspace (the parent window of this iframe)
  function postToParent(type, payload) {
    if (window.parent === window) {
      return;
    }
    window.parent.postMessage({
      token: token,
      action: { type: type, payload: payload || {} }
    }, '*');
  }

  function clearConnection() {
    state.port = '';
    state.controllerType = '';
    state.pinState = '';
    resetChecks();
    render();
  }

  var socket = io.connect('', { query: 'token=' + token });

  // Also fires after every reconnect (cncjs restart, network drop, laptop sleep):
  // the server has forgotten this socket, so ask the workspace for the port again
  socket.on('connect', function () {
    postToParent('connect');
  });

  // Never keep showing pin states we no longer receive
  socket.on('disconnect', clearConnection);

  socket.on('serialport:open', function (options) {
    state.port = options.port;
    state.controllerType = options.controllerType;
    resetChecks();
    render();
  });

  socket.on('serialport:close', clearConnection);

  // Join a port only if the server reports it open. 'open' on a port that is not
  // open would make the server try to open the machine port itself.
  var portToJoin = '';

  socket.on('serialport:list', function (ports) {
    var port = portToJoin;
    portToJoin = '';
    var isOpen = (ports || []).some(function (p) {
      return p.port === port && p.inuse;
    });
    if (port && isOpen && port !== state.port) {
      socket.emit('open', port, {});
    }
  });

  socket.on('controller:state', function (type, controllerState) {
    var status = (controllerState && controllerState.status) || {};
    state.controllerType = type;
    state.pinState = status.pinState || '';
    updateChecks();
    render();
  });

  // The workspace tells us which port it has open; we only join it.
  // Never emit 'close' here: that would close the machine port for everyone.
  window.addEventListener('message', function (event) {
    var data = event.data || {};
    var action = data.action || {};

    if (event.source !== window.parent || data.token !== token || action.type !== 'change') {
      return;
    }

    var port = (action.payload || {}).port || '';
    if (port && port !== state.port) {
      portToJoin = port;
      socket.emit('list');
    }
  });

  render();
  postToParent('resize', { scrollHeight: document.body.scrollHeight });
})();

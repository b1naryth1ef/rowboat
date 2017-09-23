import EventEmitter from 'eventemitter3';

import {getCurrentUser} from './user';

class State {
  constructor() {
    this.events = new EventEmitter();
    this.user = null;
    this.ready = false;
  }

  init() {
    getCurrentUser().then((user) => {
      this.setUser(user);
    }).catch((err) => {
      this.setUser(null);
    });
  }

  setUser(user) {
    this.user = user;
    this.events.emit('user.set', user);

    if (!this.ready) {
      this.ready = true;
      this.events.emit('ready');
    }
  }

};

export var globalState = new State;

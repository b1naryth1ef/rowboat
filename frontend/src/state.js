import EventEmitter from 'eventemitter3';
import axios from 'axios';

import User from './models/user';

class State {
  constructor() {
    this.events = new EventEmitter();
    this.user = null;
    this.ready = false;

    this._currentGuild = null;
  }

  set currentGuild(guild) {
    this._currentGuild = guild;
    this.events.emit('currentGuild.set', guild);
  }

  get currentGuild() {
    return this._currentGuild;
  }

  init() {
    if (this.ready) return;

    this.getCurrentUser().then((user) => {
      this.ready = true;
      this.events.emit('ready');
      user.getGuilds();
    });
  }

  getGuild(guildID) {
    return new Promise((resolve, reject) => {
      this.getCurrentUser().then((user) => {
        user.getGuilds().then((guilds) => {
          if (guildID in guilds) {
            resolve(guilds[guildID]);
          } else {
            reject(null);
          }
        });
      });
    });
  }

  getCurrentUser(refresh = false) {
    // If the user is already set, just fire the callback
    if (this.user && !refresh) {
      return new Promise((resolve) => {
        resolve(this.user);
      });
    }

    return new Promise((resolve) => {
      axios.get('/api/users/@me').then((res) => {
        this.user = new User(res.data);
        this.events.emit('user.set', this.user);
        resolve(this.user);
      });
    });
  }

  logout() {
    return new Promise((resolve) => {
      axios.post('/api/auth/logout').then((res) => {
        this.user = null;
        this.events.emit('user.set', this.user);
        resolve();
      });
    });
  }
};

export var globalState = new State;

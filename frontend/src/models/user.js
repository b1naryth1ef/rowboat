import axios from 'axios';
import {globalState} from '../state';
import BaseModel from './base';
import Guild from './guild';

export default class User extends BaseModel {
  constructor(obj) {
    super();

    this.id = obj.id;
    this.username = obj.username;
    this.discriminator = obj.discriminator;
    this.avatar = obj.avatar;
    this.bot = obj.bot;
    this.admin = obj.admin;

    this.guilds = null;
  }

  getGuilds(refresh = false) {
    if (this.guilds && !refresh) {
      return new Promise((resolve) => resolve(this.guilds));
    }

    return new Promise((resolve) => {
      axios.get('/api/users/@me/guilds').then((res) => {
        let guilds = res.data.map((guildData) => {
          return new Guild(guildData);
        });

        this.guilds = {}
        for (let guild of guilds) {
          this.guilds[guild.id] = guild;
        }

        this.events.emit('guilds.set', this.guilds);
        resolve(this.guilds);
      });
    });
  }
}


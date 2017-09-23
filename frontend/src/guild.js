import axios from 'axios';
import {globalState} from './state';

class Guild {
  constructor(obj) {
    this.id = obj.id;
    this.ownerID = obj.owner_id;
    this.name = obj.name;
    this.icon = obj.icon;
    this.splash = obj.splash;
    this.region = obj.region;
    this.enabled = obj.enabled;
    this.whitelist = obj.whitelist;
    this.role = obj.role;
  }

  getConfig() {
    return new Promise((resolve, reject) => {
      axios.get(`/api/guilds/${this.id}/config`).then((res) => {
        resolve(res.data);
      }).catch((err) => {
        reject();
      });
    });
  }

  putConfig(config) {
    return new Promise((resolve, reject) => {
      axios.post(`/api/guilds/${this.id}/config`, {config: config}).then((res) => {
        resolve();
      }).catch((err) => {
        reject(err.response.data);
      });
    });
  }
}


export function getGuild(guildID) {
  return new Promise((resolve, reject) => {
    axios.get(`/api/guilds/${guildID}`).then((res) => {
      resolve(new Guild(res.data));
    }).catch((err) => {
      reject();
    });
  });
}

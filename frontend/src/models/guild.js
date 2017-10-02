import axios from 'axios';
import {globalState} from '../state';
import BaseModel from './base';

export default class Guild extends BaseModel {
  constructor(obj) {
    super();
    this.fromData(obj);
    this.config = null;
  }

  fromData(obj) {
    this.id = obj.id;
    this.ownerID = obj.owner_id;
    this.name = obj.name;
    this.icon = obj.icon;
    this.splash = obj.splash;
    this.region = obj.region;
    this.enabled = obj.enabled;
    this.whitelist = obj.whitelist;
    this.role = obj.role;
    this.events.emit('update', this);
  }

  update() {
    return new Promise((resolve, reject) => {
      axios.get(`/api/guilds/${this.id}`).then((res) => {
        this.fromData(res.data);
        resolve(res.data);
      }).catch((err) => {
        reject(err.response.data);
      });
    });
  }

  getConfig(refresh = false) {
    if (this.config && !refresh) {
      return new Promise((resolve) => {
        resolve(this.config);
      });
    }

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

  getInfractions(page, limit, sorted, filtered) {
    let params = {page, limit};

    if (sorted) {
      params.sorted = JSON.stringify(sorted)
    }

    if (filtered) {
      params.filtered = JSON.stringify(filtered)
    }

    return new Promise((resolve, reject) => {
      axios.get(`/api/guilds/${this.id}/infractions`, {params: params}).then((res) => {
        resolve(res.data);
      }).catch((err) => {
        reject(err.response.data);
      });
    });
  }
}

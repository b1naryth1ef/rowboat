import EventEmitter from 'eventemitter3';

export default class BaseModel {
  constructor() {
    this.events = new EventEmitter();
  }
}

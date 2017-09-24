import React, { Component } from 'react';
import { Redirect } from 'react-router-dom'
import {globalState} from '../state';

export default class Login extends Component {
  constructor() {
    super();

    this.state = {
      user: globalState.user,
    };

    globalState.events.on('user.set', (user) => {
      this.setState({user: user});
    });

    globalState.init();
  }

  render() {
    if (this.state.user) {
      return <Redirect to='/' />;
    }

    return (
      <div class="container">
        <div class="row">
          <div class="col-md-4 col-md-offset-4">
            <div class="login-panel panel panel-default">
              <div class="panel-heading">
                <h3 class="panel-title">Login with Discord</h3>
              </div>
              <div class="panel-body">
                <a href="/api/auth/discord">
                  <img src="https://discordapp.com/assets/bb408e0343ddedc0967f246f7e89cebf.svg" height="256" width="256" style="margin: auto; display: block;"/>
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }
}

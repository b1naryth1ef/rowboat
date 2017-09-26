import React, { Component } from 'react';

import {globalState} from '../state';
import Topbar from './topbar';
import Dashboard from './dashboard';
import Login from './login';
import GuildOverview from './guild_overview';
import GuildConfigEdit from './guild_config_edit';
import GuildInfractions from './guild_infractions';
import GuildStats from './guild_stats';
import { BrowserRouter, Route, Switch, Redirect } from 'react-router-dom';

class AppWrapper extends Component {
  constructor() {
    super();

    this.state = {
      ready: globalState.ready,
      user: globalState.user,
    };

    if (!globalState.ready) {
      globalState.events.on('ready', () => {
        this.setState({
          ready: true,
        });
      });

      globalState.events.on('user.set', (user) => {
        this.setState({
          user: user,
        });
      });

      globalState.init();
    }
  }

  render() {
    if (!this.state.ready) {
      return <div><h1>Loading...</h1></div>;
    }

    if (this.state.ready && !this.state.user) {
      return <Redirect to='/login' />;
    }

    return (
      <div id="wrapper">
        <Topbar />
        <div id="page-wrapper">
          <this.props.view params={this.props.params} />
        </div>
      </div>
    );
  }
}

function wrapped(component) {
  function result(props) {
    return <AppWrapper view={component} params={props.match.params} />;
  }
  return result;
}

export default function router() {
  return (
    <BrowserRouter>
      <Switch>
        <Route exact path='/login' component={Login} />
        <Route exact path='/guilds/:gid/stats' component={wrapped(GuildStats)} />
        <Route exact path='/guilds/:gid/infractions' component={wrapped(GuildInfractions)} />
        <Route exact path='/guilds/:gid/config' component={wrapped(GuildConfigEdit)} />
        <Route exact path='/guilds/:gid' component={wrapped(GuildOverview)} />
        <Route exact path='/' component={wrapped(Dashboard)} />
      </Switch>
    </BrowserRouter>
  );
}

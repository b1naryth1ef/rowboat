import { h, Component } from 'preact';

import {globalState} from '../state';
import Topbar from './topbar';
import Dashboard from './dashboard';
import Login from './login';
import GuildOverview from './guild_overview';
import GuildConfigEdit from './guild_config_edit';
import GuildInfractions from './guild_infractions';
import { BrowserRouter, Route, Switch, Redirect } from 'react-router-dom'

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

  render(props, state) {
    if (!state.ready) {
      return <div><h1>Loading...</h1></div>;
    }

    if (state.ready && !state.user) {
      return <Redirect to='/login' />;
    }

    return (
      <div id="wrapper">
        <Topbar />
        <div id="page-wrapper">
          <props.view params={props.params} />
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
        <Route path='/login' component={Login} />
        <Route path='/guilds/:gid/infractions' component={wrapped(GuildInfractions)} />
        <Route path='/guilds/:gid/config' component={wrapped(GuildConfigEdit)} />
        <Route path='/guilds/:gid' component={wrapped(GuildOverview)} />
        <Route path='/' component={wrapped(Dashboard)} />
      </Switch>
    </BrowserRouter>
  );
}

import { h, render, Component } from 'preact';
import Sidebar from './sidebar';
import {globalState} from '../state';
import {browserHistory} from 'react-router';

class Topbar extends Component {
  onLogoutClicked() {
    state.user.logout().then(() => {
      browserHistory.push('/login');
    });
  }

  render() {
		return(
			<nav class="navbar navbar-default navbar-static-top" role="navigation" style="margin-bottom: 0">
				<div class="navbar-header">
					<a class="navbar-brand">Rowboat</a>
				</div>

				<ul class="nav navbar-top-links navbar-right">
					<li><a onClick={this.onLogoutClicked}><i class="fa fa-sign-out fa-fw"></i></a></li>
				</ul>

        <Sidebar />
			</nav>
    );
  }
}

export default Topbar;

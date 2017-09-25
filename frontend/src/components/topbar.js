import React, { Component } from 'react';
import Sidebar from './sidebar';
import {globalState} from '../state';
import {withRouter} from 'react-router';

class Topbar extends Component {
  constructor() {
    super();
    this.state = {
      showAllGuilds: globalState.showAllGuilds,
    };

    globalState.events.on('showAllGuilds.set', (value) => this.setState({showAllGuilds: value}));
  }

  onLogoutClicked() {
    globalState.logout().then(() => {
      this.props.history.push('/login');
    });
  }

  onExpandClicked() {
    globalState.showAllGuilds = !globalState.showAllGuilds;
  }

  render() {
    const expandIcon = this.state.showAllGuilds ? 'fa fa-folder-open-o fa-fw' : ' fa fa-folder-o fa-fw';

		return(
			<nav className="navbar navbar-default navbar-static-top" role="navigation" style={{marginBottom: 0}}>
				<div className="navbar-header">
					<a className="navbar-brand">Rowboat</a>
				</div>

				<ul className="nav navbar-top-links navbar-right">
					<li><a onClick={this.onLogoutClicked.bind(this)}><i className="fa fa-sign-out fa-fw"></i></a></li>
					<li><a onClick={this.onExpandClicked.bind(this)}><i className={expandIcon}></i></a></li>
				</ul>

        <Sidebar />
			</nav>
    );
  }
}

export default withRouter(Topbar);

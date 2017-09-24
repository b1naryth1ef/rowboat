import React, { Component } from 'react';
import Sidebar from './sidebar';
import {globalState} from '../state';
import {withRouter} from 'react-router';

class Topbar extends Component {
  onLogoutClicked() {
    globalState.logout().then(() => {
      this.props.history.push('/login');
    });
  }

  render() {
		return(
			<nav className="navbar navbar-default navbar-static-top" role="navigation" style={{marginBottom: 0}}>
				<div className="navbar-header">
					<a className="navbar-brand">Rowboat</a>
				</div>

				<ul className="nav navbar-top-links navbar-right">
					<li><a onClick={this.onLogoutClicked.bind(this)}><i className="fa fa-sign-out fa-fw"></i></a></li>
				</ul>

        <Sidebar />
			</nav>
    );
  }
}

export default withRouter(Topbar);

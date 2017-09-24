import React, { Component } from 'react';
import { Link } from 'react-router-dom'
import {globalState} from '../state';

class SidebarLink extends Component {
  render () {
    const iconClass = `fa fa-${this.props.icon} fa-fw`;

    return (
      <li>
        <Link to={this.props.to}>
          <i className={iconClass}></i> {this.props.text}
        </Link>
      </li>
    );
  }
}


class GuildLinks extends Component {
  render() {
    let links = [];

    if (this.props.active) {
      links.push(
        <SidebarLink icon='info' to={'/guilds/' + this.props.guild.id} text='Information' key='info' />
      );

      links.push(
        <SidebarLink icon='cog' to={'/guilds/' + this.props.guild.id + '/config'} text='Config' key='config' />
      );

      links.push(
        <SidebarLink icon='ban' to={'/guilds/' + this.props.guild.id + '/infractions'} text='Infractions' key='infractions' />
      );
    }

    return (
      <li>
        <Link to={'/guilds/' + this.props.guild.id}>
          {this.props.guild.name}
        </Link>
        <ul className="nav nav-second-level collapse in">
          {links}
        </ul>
      </li>
    );
  }
}


class Sidebar extends Component {
  constructor() {
    super();

    this.state = {
      guilds: null,
      currentGuildID: globalState.currentGuild ? globalState.currentGuild.id : null,
    };

    globalState.getCurrentUser().then((user) => {
      user.getGuilds().then((guilds) => {
        this.setState({guilds});
      });
    });

    globalState.events.on('currentGuild.set', (guild) => {
      this.setState({currentGuildID: guild ? guild.id : null});
    });
  }

  render() {
    let sidebarLinks = [];

    sidebarLinks.push(
      <SidebarLink icon='dashboard' to='/' text='Dashboard' key='dashboard' />
    );

    if (this.state.guilds) {
      for (let guild of Object.values(this.state.guilds)) {
        // Only show the active guild for users with a lot of them
        if (Object.keys(this.state.guilds).length > 10 && guild.id != this.state.currentGuildID) continue;
        sidebarLinks.push(<GuildLinks guild={guild} active={guild.id == this.state.currentGuildID} key={guild.id} />);
      }
    }

    return (<div className="navbar-default sidebar" role="navigation">
      <div className="sidebar-nav navbar-collapse">
        <ul className="nav in" id="side-menu">
          {sidebarLinks}
        </ul>
      </div>
    </div>);
  }
}

export default Sidebar;

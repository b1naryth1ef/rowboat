import { h, render, Component } from 'preact';
import { Link } from 'react-router-dom'
import {globalState} from '../state';

class SidebarLink extends Component {
  render (props, state) {
    const iconClass = `fa fa-${props.icon} fa-fw`;

    return (
      <li>
        <Link to={props.to}>
          <i class={iconClass}></i> {props.text}
        </Link>
      </li>
    );
  }
}


class GuildLinks extends Component {
  render(props, state) {
    let links = [];

    if (props.active) {
      links.push(
        <SidebarLink icon='info' to={'/guilds/' + props.guild.id} text='Information' />
      );

      links.push(
        <SidebarLink icon='cog' to={'/guilds/' + props.guild.id + '/config'} text='Config' />
      );

      links.push(
        <SidebarLink icon='ban' to={'/guilds/' + props.guild.id + '/infractions'} text='Infractions' />
      );
    }

    return (
      <li>
        <Link to={'/guilds/' + props.guild.id}>
          {props.guild.name}
        </Link>
        <ul class="nav nav-second-level collapse in">
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

  render(props, state) {
    let sidebarLinks = [];

    sidebarLinks.push(
      <SidebarLink icon='dashboard' to='/' text='Dashboard' />
    );

    if (state.guilds) {
      for (let guild of Object.values(state.guilds)) {
        sidebarLinks.push(<GuildLinks guild={guild} active={guild.id == state.currentGuildID} />);
      }
    }

    return (<div class="navbar-default sidebar" role="navigation">
      <div class="sidebar-nav navbar-collapse">
        <ul class="nav in" id="side-menu">
          {sidebarLinks}
        </ul>
      </div>
    </div>);
  }
}

export default Sidebar;

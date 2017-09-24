import React, { Component } from 'react';
import {globalState} from '../state';
import {withRouter} from 'react-router';

class GuildWidget extends Component {
  render() {
    const source = `https://discordapp.com/api/guilds/${this.props.guildID}/widget.png?style=banner2`;
    return (<img src={source} alt="(Guild must have widget enabled)" />);
  }
}

class GuildIcon extends Component {
  render() {
    const source = `https://cdn.discordapp.com/icons/${this.props.guildID}/${this.props.guildIcon}.png`;
    return <img src={source} alt="No Icon" />;
  }
}

class GuildSplash extends Component {
  render() {
    const source = `https://cdn.discordapp.com/splashes/${this.props.guildID}/${this.props.guildSplash}.png`;
    return <img src={source} alt="No Splash" />;
  }
}

class GuildOverviewInfoTable extends Component {
  onPurchase() {
    fastspring.builder.tag({
      user_id: globalState.user.id,
      guild_id: this.props.guild.id,
    })

    fastspring.builder.add('rowboat-premium');
    fastspring.builder.checkout();
  }

  onCancel() {
    this.props.guild.cancelPremium();
  }

  render() {
    let premium = null;

    if (this.props.guild.premium.active) {
      let parts = [];

      parts.push(
        <b key='active'>Active!</b>
      );

      parts.push(<br key='br1' />);

      parts.push(
        <i key='by'>Purchased by {this.props.guild.premium.info.user.id}</i>
      );

      if (globalState.user.id == this.props.guild.premium.info.user.id || globalState.user.admin) {
        parts.push(<br key='br2' />);
        parts.push(
          <a key='cancel' href='#' onClick={this.onCancel.bind(this)}>Cancel Premium</a>
        );
      }

      premium = (<span>{parts}</span>);
    } else {
      // premium = <a href='#' onClick={this.onPurchase.bind(this)}>Purchase Rowboat Premium</a>;
      premium = <i>Premium Coming Soon</i>;
    }

    return (
      <table className="table table-striped table-bordered table-hover">
        <thead></thead>
        <tbody>
          <tr>
            <td>ID</td>
            <td>{this.props.guild.id}</td>
          </tr>
          <tr>
            <td>Owner</td>
            <td>{this.props.guild.ownerID}</td>
          </tr>
          <tr>
            <td>Region</td>
            <td>{this.props.guild.region}</td>
          </tr>
          <tr>
            <td>Icon</td>
            <td><GuildIcon guildID={this.props.guild.id} guildIcon={this.props.guild.icon} /></td>
          </tr>
          <tr>
            <td>Splash</td>
            <td><GuildSplash guildID={this.props.guild.id} guildSplash={this.props.guild.splash} /></td>
          </tr>
          <tr>
            <td>Premium</td>
            <td>{premium}</td>
          </tr>
        </tbody>
      </table>
    );
  }
}

export default class GuildOverview extends Component {
  constructor() {
    super();

    this.state = {
      guild: null,
    };
  }

  componentWillMount() {
    globalState.getGuild(this.props.params.gid).then((guild) => {
      guild.events.on('update', (guild) => this.setState({guild}));
      globalState.currentGuild = guild;
      this.setState({guild});
    }).catch((err) => {
      console.error('Failed to load guild', this.props.params.gid, err);
    });
  }

  componentWillUnmount() {
    globalState.currentGuild = null;
  }

  render() {
    if (!this.state.guild) {
      return <h3>Loading...</h3>;
    }

    const OverviewTable = withRouter(GuildOverviewInfoTable);

    return (<div>
      <div className="row">
        <div className="col-lg-12">
          <div className="panel panel-default">
            <div className="panel-heading">Guild Banner</div>
            <div className="panel-body">
              <GuildWidget guildID={this.state.guild.id} />
            </div>
          </div>
          <div className="panel panel-default">
            <div className="panel-heading">Guild Info</div>
            <div className="panel-body">
              <div className="table-responsive">
                <OverviewTable guild={this.state.guild} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>);
  }
}

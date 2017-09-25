import React, { Component } from 'react';
import {globalState} from '../state';
import {withRouter} from 'react-router';
import {PREMIUM_ENABLED} from 'config';

class GuildWidget extends Component {
  render() {
    const source = `https://discordapp.com/api/guilds/${this.props.guildID}/widget.png?style=banner2`;
    return (<img src={source} alt="(Guild must have widget enabled)" />);
  }
}

class GuildIcon extends Component {
  render() {
    if (this.props.guildIcon) {
      const source = `https://cdn.discordapp.com/icons/${this.props.guildID}/${this.props.guildIcon}.png`;
      return <img src={source} alt="No Icon" />;
    } else {
      return <i>No Icon</i>;
    }
  }
}

class GuildSplash extends Component {
  render() {
    if (this.props.guildSplash) {
      const source = `https://cdn.discordapp.com/splashes/${this.props.guildID}/${this.props.guildSplash}.png`;
      return <img src={source} alt="No Splash" />;
    } else {
      return <i>No Splash</i>;
    }
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

  onGive() {
    this.props.guild.givePremium();
  }

  onCancel() {
    this.props.guild.cancelPremium();
  }

  render() {
    let parts = [];

    if (this.props.guild.premium.active) {
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
    } else {
      if (PREMIUM_ENABLED) {
        parts.push(
          <a key='purchase' href='#' onClick={this.onPurchase.bind(this)}>Purchase Rowboat Premium</a>
        );

        if (globalState.user.admin) {
          parts.push(<br key='br3' />);
          parts.push(
            <a key='give' href='#' onClick={this.onGive.bind(this)}>Give Premium</a>
          );
        }
      } else {
        parts.push(
          <i key='soon'>Premium Coming Soon</i>
        );
      }
    }

    const premium = (<span>{parts}</span>);

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

  ensureGuild() {
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
    if (!this.state.guild || this.state.guild.id != this.props.params.gid) {
      this.ensureGuild();
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

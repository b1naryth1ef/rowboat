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
  render() {
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

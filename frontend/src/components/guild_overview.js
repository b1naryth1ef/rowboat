import { h, render, Component } from 'preact';
import {globalState} from '../state';

class GuildWidget extends Component {
  render(props, state) {
    const source = `https://discordapp.com/api/guilds/${props.guildID}/widget.png?style=banner2`;
    return (<img src={source} alt="(Guild must have widget enabled)" />);
  }
}

class GuildIcon extends Component {
  render(props, state) {
    const source = `https://cdn.discordapp.com/icons/${props.guildID}/${props.guildIcon}.png`;
    return <img src={source} alt="No Icon" />;
  }
}

class GuildSplash extends Component {
  render(props, state) {
    const source = `https://cdn.discordapp.com/splashes/${props.guildID}/${props.guildSplash}.png`;
    return <img src={source} alt="No Splash" />;
  }
}

class GuildOverviewInfoTable extends Component {
  render(props, state) {
    return (
      <table class="table table-striped table-bordered table-hover">
        <thead></thead>
        <tbody>
          <tr>
            <td>ID</td>
            <td>{props.guild.id}</td>
          </tr>
          <tr>
            <td>Owner</td>
            <td>{props.guild.ownerID}</td>
          </tr>
          <tr>
            <td>Region</td>
            <td>{props.guild.region}</td>
          </tr>
          <tr>
            <td>Icon</td>
            <td><GuildIcon guildID={props.guild.id} guildIcon={props.guild.icon} /></td>
          </tr>
          <tr>
            <td>Splash</td>
            <td><GuildSplash guildID={props.guild.id} guildSplash={props.guild.splash} /></td>
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
      this.setState({guild});
    }).catch((err) => {
      console.error('Failed to load guild', this.props.params.gid);
    });
  }

  render(props, state) {
    if (!state.guild) {
      return <h3>Loading...</h3>;
    }

    return (<div>
      <div class="row">
        <div class="col-lg-12">
          <div class="panel panel-default">
            <div class="panel-heading">Guild Banner</div>
            <div class="panel-body">
              <GuildWidget guildID={state.guild.id} />
            </div>
          </div>
          <div class="panel panel-default">
            <div class="panel-heading">Guild Info</div>
            <div class="panel-body">
              <div class="table-responsive">
                <GuildOverviewInfoTable guild={state.guild} />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>);
  }
}

import { h, render, Component } from 'preact';
import { state, VIEWS } from '../state';
import { Link } from 'react-router-dom'

class GuildTableRowActions extends Component {
  render(props, state) {
    return (
      <div>
        <a style="padding-left: 4px">
          <Link to={`/guilds/${props.guild.id}`}>
            <button type="button" class="btn btn-success btn-circle"><i class="fa fa-info"></i></button>
          </Link>
        </a>
			  <a style="padding-left: 4px">
          <Link to={`/guilds/${props.guild.id}/config`}>
            <button type="button" class="btn btn-info btn-circle"><i class="fa fa-edit"></i></button>
          </Link>
        </a>
      </div>
    );
  }

  onInfo(guild) {
    state.setView(VIEWS.GUILD_OVERVIEW, {
      guild: guild,
    });
  }

  onEdit(guild) {
    state.setView(VIEWS.GUILD_CONFIG_EDIT, {
      guild: guild,
    });
  }
}

class GuildTableRow extends Component {
  render(props, state) {
    return (
      <tr>
        <td>{props.guild.id}</td>
        <td>{props.guild.name}</td>
        <td><GuildTableRowActions guild={props.guild} /></td>
      </tr>
    );
  }
}

class GuildsTable extends Component {
  render(props, state) {
    if (!props.guilds) {
      return <h3>Loading...</h3>;
    }

    var rows = [];
    Object.values(props.guilds).map((guild) => {
      rows.push(<GuildTableRow guild={guild} />);
    });

    return (
      <div class="table-responsive">
        <table class="table table-sriped table-bordered table-hover">
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows}
          </tbody>
        </table>
      </div>
    );
  }
}

export default GuildsTable;

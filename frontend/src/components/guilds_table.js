import React, { Component } from 'react';
import { state, VIEWS } from '../state';
import { Link } from 'react-router-dom'

class GuildTableRowActions extends Component {
  render(props, state) {
    return (
      <div>
        <Link to={`/guilds/${this.props.guild.id}`} style={{paddingLeft: '4px'}}>
          <button type="button" className="btn btn-success btn-circle"><i className="fa fa-info"></i></button>
        </Link>
        <Link to={`/guilds/${this.props.guild.id}/config`} style={{paddingLeft: '4px'}}>
          <button type="button" className="btn btn-info btn-circle"><i className="fa fa-edit"></i></button>
        </Link>
        <Link to={`/guilds/${this.props.guild.id}/infractions`} style={{paddingLeft: '4px'}}>
          <button type="button" className="btn btn-danger btn-circle"><i className="fa fa-ban"></i></button>
        </Link>
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
  render() {
    return (
      <tr>
        <td>{this.props.guild.id}</td>
        <td>{this.props.guild.premium.active ? 'Yes' : 'No'}</td>
        <td>{this.props.guild.name}</td>
        <td><GuildTableRowActions guild={this.props.guild} /></td>
      </tr>
    );
  }
}

class GuildsTable extends Component {
  render() {
    if (!this.props.guilds) {
      return <h3>Loading...</h3>;
    }

    var rows = [];
    Object.values(this.props.guilds).map((guild) => {
      rows.push(<GuildTableRow guild={guild} key={guild.id} />);
    });

    return (
      <div className="table-responsive">
        <table className="table table-sriped table-bordered table-hover">
          <thead>
            <tr>
              <th>ID</th>
              <th>Premium</th>
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

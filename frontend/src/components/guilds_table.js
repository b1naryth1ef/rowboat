import React, { Component } from 'react';
import { state, VIEWS } from '../state';
import { Link } from 'react-router-dom';
import {globalState} from '../state';
import sortBy from 'lodash/sortBy';

class GuildTableRowActions extends Component {
  render(props, state) {
    let parts = [];

    parts.push(
      <Link key="1" to={`/guilds/${this.props.guild.id}`} style={{paddingLeft: '4px'}}>
        <button type="button" className="btn btn-success btn-circle">
        <i className="fa fa-info"></i></button>
      </Link>
    );

    parts.push(
      <Link key="2" to={`/guilds/${this.props.guild.id}/config`} style={{paddingLeft: '4px'}}>
        <button type="button" className="btn btn-info btn-circle">
        <i className="fa fa-edit"></i></button>
      </Link>
    );

    parts.push(
      <Link key="3" to={`/guilds/${this.props.guild.id}/infractions`} style={{paddingLeft: '4px'}}>
        <button type="button" className="btn btn-warning btn-circle">
        <i className="fa fa-ban"></i></button>
      </Link>
    );

    if (globalState.user && globalState.user.admin) {
      parts.push(
        <a key="4" href="#" style={{paddingLeft: '4px'}} onClick={this.onDelete.bind(this)}>
          <button type="button" className="btn btn-danger btn-circle">
          <i className="fa fa-trash-o"></i></button>
        </a>
      );
    }

    return (
      <div>
        {parts}
      </div>
    );
  }

  onDelete() {
    this.props.guild.delete().then(() => {
      window.location.reload();
    });
  }
}

class GuildTableRow extends Component {
  render() {
    return (
      <tr>
        <td>{this.props.guild.id}</td>
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

    let guilds = sortBy(Object.values(this.props.guilds), (i) => i.id);

    var rows = [];
    guilds.map((guild) => {
      rows.push(<GuildTableRow guild={guild} key={guild.id} />);
    });

    return (
      <div className="table-responsive">
        <table className="table table-sriped table-bordered table-hover">
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

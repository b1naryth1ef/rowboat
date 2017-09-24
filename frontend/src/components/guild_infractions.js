import React, { Component } from 'react';
import debounce from 'lodash/debounce';
import {globalState} from '../state';
import ReactTable from "react-table";

class GuildInfractionsTable extends Component {
  constructor() {
    super();

    this.state = {
      data: [],
      loading: true,
    };
  }

  render() {
    return (
      <ReactTable
        data={this.state.data}
        columns={[
          {Header: "ID", accessor: "id"},
          {Header: "User", columns: [
            {Header: "ID", accessor: "user.id", id: "user_id"},
            {
              Header: "Tag",
              id: "user_tag",
              accessor: d => d.user.username + '#' + d.user.discriminator,
              filterable: false,
              sortable: false,
            }
          ]},
          {Header: "Actor", columns: [
            {Header: "ID", accessor: "actor.id", id: "actor_id"},
            {
              Header: "Tag",
              id: "actor_tag",
              accessor: d => d.actor.username + '#' + d.actor.discriminator,
              filterable: false,
              sortable: false,
            }
          ]},
          {Header: "Created At", accessor: "created_at", filterable: false},
          {Header: "Expires At", accessor: "expires_at", filterable: false},
          {Header: "Type", accessor: "type.name", id: "type"},
          {Header: "Reason", accessor: "reason", sortable: false},
          {Header: "Active", id: "active", accessor: d => d.active ? 'Active' : 'Inactive', sortable: false, filterable: false},
        ]}
        pages={10000}
        loading={this.state.loading}
        manual
        onFetchData={debounce(this.onFetchData.bind(this), 500)}
        filterable
        className="-striped -highlight"
      />
    );
  }

  onFetchData(state, instance) {
    this.setState({loading: true});

    this.props.guild.getInfractions(state.page, state.pageSize, state.sorted, state.filtered).then((data) => {
      this.setState({
        data: data,
        loading: false,
      });
    });
  }
}

export default class GuildInfractions extends Component {
  constructor() {
    super();

    this.state = {
      guild: null,
    };
  }

  componentWillMount() {
    globalState.getGuild(this.props.params.gid).then((guild) => {
      globalState.currentGuild = guild;
      this.setState({guild});
    }).catch((err) => {
      console.error('Failed to load guild', this.props.params.gid);
    });
  }

  componentWillUnmount() {
    globalState.currentGuild = null;
  }

  render() {
    if (!this.state.guild) {
      return <h3>Loading...</h3>;
    }

    return (
      <div className="row">
        <div className="col-lg-12">
          <div className="panel panel-default">
            <div className="panel-heading">Infractions</div>
            <div className="panel-body">
              <GuildInfractionsTable guild={this.state.guild} />
            </div>
          </div>
        </div>
      </div>
    );
  }
}

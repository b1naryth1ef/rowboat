import { h, render, Component } from 'preact';
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

  render(props, state) {
    console.log('Rerendering;', state);
    return (
      <ReactTable
        data={state.data}
        columns={[
          {Header: "ID", accessor: "id"},
          {Header: "User", columns: [
            {Header: "ID", accessor: "user.id"},
            {Header: "Tag", id: "user.tag", accessor: d => d.user.username + d.user.discriminator}
          ]},
          {Header: "Actor", columns: [
            {Header: "ID", accessor: "actor.id"},
            {Header: "Tag", id: "actor.tag", accessor: d => d.actor.username + d.actor.discriminator}
          ]},
          {Header: "Type", accessor: "type.name"},
          {Header: "Reason", accessor: "reason"}
        ]}
        pages={-1}
        loading={state.loading}
        manual
        onFetchData={this.onFetchData.bind(this)}
        filterable
        className="-striped -highlight"
      />
    );
  }

  onFetchData(state, instance) {
    this.setState({loading: true});

    console.log(state.sorted);
    console.log(state.filtered);

    this.props.guild.getInfractions(state.page, state.pageSize).then((data) => {
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

  render(props, state) {
    if (!state.guild) {
      return <h3>Loading...</h3>;
    }

    return (
      <div class="row">
        <div class="col-lg-12">
          <div class="panel panel-default">
            <div class="panel-heading">Infractions</div>
            <div class="panel-body">
              <GuildInfractionsTable guild={state.guild} />
            </div>
          </div>
        </div>
      </div>
    );
  }
}

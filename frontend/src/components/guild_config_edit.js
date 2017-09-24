import { h, Component } from 'preact';
import AceEditor from 'react-ace';
import {globalState} from '../state';

import 'brace/mode/yaml'
import 'brace/theme/monokai'

export default class GuildConfigEdit extends Component {
  constructor() {
    super();

    this.messageTimer = null;
    this.initialConfig = null;

    this.state = {
      message: null,
      guild: null,
      contents: null,
      hasUnsavedChanges: false,
    }
  }

  componentWillMount() {
    globalState.getGuild(this.props.params.gid).then((guild) => {
      globalState.currentGuild = guild;

      guild.getConfig(true).then((config) => {
        this.initialConfig = config.contents;

        this.setState({
          guild: guild,
          contents: config.contents,
        });
      });
    }).catch((err) => {
      console.error('Failed to find guild for config edit', this.props.params.gid);
    });
  }

  componentWillUnmount() {
    globalState.currentGuild = null;
  }

  onEditorChange(newValue) {
    let newState = {contents: newValue, hasUnsavedChanges: false};
    if (this.initialConfig != newValue) {
      newState.hasUnsavedChanges = true;
    }
    this.setState(newState);
  }

  onSave() {
    this.state.guild.putConfig(this.state.contents).then(() => {
      this.initialConfig = this.state.contents;
      this.setState({
        hasUnsavedChanges: false,
      });
      this.renderMessage('success', 'Saved Configuration!');
    }).catch((err) => {
      this.renderMessage('danger', `Failed to save configuration: ${err}`);
    });
  }

  renderMessage(type, contents) {
    this.setState({
      message: {
        type: type,
        contents: contents,
      }
    })

    if (this.messageTimer) clearTimeout(this.messageTimer);

    this.messageTimer = setTimeout(() => {
      this.setState({
        message: null,
      });
      this.messageTimer = null;
    }, 5000);
  }

  render(props, state) {
    return (<div>
      {state.message && <div class={"alert alert-" + state.message.type}>{state.message.contents}</div>}
      <div class="row">
        <div class="col-md-12">
          <div class="panel panel-default">
            <div class="panel-heading">
              Configuration Editor
            </div>
            <div class="panel-body">
              <AceEditor
                mode="yaml"
                theme="monokai"
                width="100%"
                value={state.contents == null ? '' : state.contents}
                onChange={(newValue) => this.onEditorChange(newValue)}
              />
            </div>
            <div class="panel-footer">
              {
                state.guild && state.guild.role != 'viewer' &&
                  <button onClick={() => this.onSave()} type="button" class="btn btn-success btn-circle btn-lg">
                  <i class="fa fa-check"></i>
                </button>
              }
              { state.hasUnsavedChanges && <i style="padding-left: 10px;">Unsaved Changes!</i>}
            </div>
          </div>
        </div>
      </div>
    </div>);
  }
}

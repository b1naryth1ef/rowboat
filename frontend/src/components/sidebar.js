import { h, render, Component } from 'preact';
import { Link } from 'react-router-dom'

class Sidebar extends Component {
  render(props, state) {
    return (<div class="navbar-default sidebar" role="navigation">
      <div class="sidebar-nav navbar-collapse">
        <ul class="nav in" id="side-menu">
          <li>
            <Link to="/">
              <i class="fa fa-dashboard fa-fw"></i> Dashboard
            </Link>
          </li>
        </ul>
      </div>
    </div>);
  }
}

export default Sidebar;

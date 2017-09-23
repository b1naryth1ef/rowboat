import { h, render, Component } from 'preact';

class PageHeader extends Component {
  render(props, state) {
		return (
			<div class="row">
				<div class="col-lg-12">
					<h1 class="page-header">{props.name}</h1>
				</div>
			</div>
    );
  }
}

export default PageHeader;

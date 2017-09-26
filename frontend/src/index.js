import React from 'react';
import ReactDOM from 'react-dom';
import { FASTSPRING_STOREFRONT } from 'config';

function init() {
  var head = document.getElementsByTagName('head')[0];
  var script = document.createElement('script');

  script.id = 'fsc-api';
  script.src = 'https://d1f8f9xcsvx3ha.cloudfront.net/sbl/0.7.4/fastspring-builder.min.js';
  script.type = 'text/javascript';
  script.dataset.storefront = FASTSPRING_STOREFRONT;
  head.appendChild(script);

	// HMR requires that this be a require()
	let App = require('./components/app').default;
  ReactDOM.render(<App />, document.getElementById('app'));
}

init();

if (module.hot) module.hot.accept('./components/app', init);

var path = require('path');

module.exports = {
	// entry file - starting point for the app
	entry: './src',

	// where to dump the output of a production build
	output: {
		path: path.join(__dirname, 'build'),
		filename: 'bundle.js'
	},

	module: {
		rules: [
      {
        test: /\.css$/,
        loader: 'style-loader'
      },
      {
        test: /\.css$/,
        loader: 'css-loader',
        query: {
          modules: true,
          localIdentName: '[name]'
        }
      },
			{
				test: /\.jsx?/i,
				loader: 'babel-loader',
				options: {
					presets: [
						'es2015'
					],
					plugins: [
						['transform-react-jsx']
					]
				}
			}
		]
	},

	// enable Source Maps
	devtool: 'source-map',

	devServer: {
    host: '0.0.0.0',
    disableHostCheck: true,
		// serve up any static files from src/
		contentBase: path.join(__dirname, 'src'),

		// enable gzip compression:
		compress: true,

		// enable pushState() routing, as used by preact-router et al:
		historyApiFallback: true,

    proxy: {
      '/api': {
        target: 'http://localhost:8686',
        secure: false
      }
    }
	},

	// resolve: {
	// 		alias: {
	// 				'react': 'preact-compat',
	// 				'react-dom': 'preact-compat',
	// 				// Not necessary unless you consume a module using `createClass`
	// 				'create-react-class': 'preact-compat/lib/create-react-class'
	// 		}
	// }
};

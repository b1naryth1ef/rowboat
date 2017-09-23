import yaml

with open('config.yaml', 'r') as f:
    loaded = yaml.load(f.read())
    locals().update(loaded)

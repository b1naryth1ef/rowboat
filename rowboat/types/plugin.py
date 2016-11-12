import six

from rowboat.types import SlottedModel


class PluginConfig(SlottedModel):
    def load(self, obj, *args, **kwargs):
        kwargs['skip'] = [k for k, v in six.iteritems(self.fields) if v.metadata.get('private')]
        return super(PluginConfig, self).load(obj, *args, **kwargs)

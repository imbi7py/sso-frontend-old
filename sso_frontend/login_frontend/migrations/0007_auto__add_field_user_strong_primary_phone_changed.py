# -*- coding: utf-8 -*-
from south.utils import datetime_utils as datetime
from south.db import db
from south.v2 import SchemaMigration
from django.db import models


class Migration(SchemaMigration):

    def forwards(self, orm):
        # Adding field 'User.strong_primary_phone_changed'
        db.add_column(u'login_frontend_user', 'strong_primary_phone_changed',
                      self.gf('django.db.models.fields.BooleanField')(default=False),
                      keep_default=False)


    def backwards(self, orm):
        # Deleting field 'User.strong_primary_phone_changed'
        db.delete_column(u'login_frontend_user', 'strong_primary_phone_changed')


    models = {
        u'login_frontend.browser': {
            'Meta': {'object_name': 'Browser'},
            'auth_level': ('django.db.models.fields.DecimalField', [], {'default': '0', 'max_digits': '2', 'decimal_places': '0'}),
            'auth_level_valid_until': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'auth_state': ('django.db.models.fields.DecimalField', [], {'default': '0', 'max_digits': '2', 'decimal_places': '0'}),
            'auth_state_valid_until': ('django.db.models.fields.DateTimeField', [], {'null': 'True', 'blank': 'True'}),
            'bid': ('django.db.models.fields.CharField', [], {'max_length': '37', 'primary_key': 'True'}),
            'created': ('django.db.models.fields.DateTimeField', [], {'auto_now_add': 'True', 'blank': 'True'}),
            'modified': ('django.db.models.fields.DateTimeField', [], {'auto_now': 'True', 'blank': 'True'}),
            'save_browser': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'ua': ('django.db.models.fields.CharField', [], {'max_length': '250'}),
            'username': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['login_frontend.User']", 'null': 'True'})
        },
        u'login_frontend.browserusers': {
            'Meta': {'object_name': 'BrowserUsers'},
            'auth_timestamp': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'browser': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['login_frontend.Browser']"}),
            u'id': ('django.db.models.fields.AutoField', [], {'primary_key': 'True'}),
            'max_auth_level': ('django.db.models.fields.CharField', [], {'default': '0', 'max_length': '1'}),
            'username': ('django.db.models.fields.related.ForeignKey', [], {'to': u"orm['login_frontend.User']"})
        },
        u'login_frontend.user': {
            'Meta': {'object_name': 'User'},
            'strong_authy_id': ('django.db.models.fields.CharField', [], {'max_length': '30', 'null': 'True', 'blank': 'True'}),
            'strong_configured': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'strong_primary_phone': ('django.db.models.fields.CharField', [], {'max_length': '30', 'unique': 'True', 'null': 'True', 'blank': 'True'}),
            'strong_primary_phone_changed': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'strong_primary_phone_refresh': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'strong_secondary_phone': ('django.db.models.fields.CharField', [], {'max_length': '30', 'unique': 'True', 'null': 'True', 'blank': 'True'}),
            'strong_secondary_phone_refresh': ('django.db.models.fields.DateTimeField', [], {'null': 'True'}),
            'strong_sms_always': ('django.db.models.fields.BooleanField', [], {'default': 'False'}),
            'username': ('django.db.models.fields.CharField', [], {'max_length': '50', 'primary_key': 'True'})
        }
    }

    complete_apps = ['login_frontend']
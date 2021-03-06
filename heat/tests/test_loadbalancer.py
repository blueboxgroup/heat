#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import copy

import mock
import mox
from oslo.config import cfg

from heat.common import exception
from heat.common import template_format
from heat.engine.clients.os import glance
from heat.engine.clients.os import nova
from heat.engine import resource
from heat.engine.resources.aws import wait_condition_handle as aws_wch
from heat.engine.resources import instance
from heat.engine.resources import loadbalancer as lb
from heat.engine import rsrc_defn
from heat.engine import scheduler
from heat.tests import common
from heat.tests import utils
from heat.tests.v1_1 import fakes as fakes_v1_1


lb_template = '''
{
  "AWSTemplateFormatVersion": "2010-09-09",
  "Description": "LB Template",
  "Parameters" : {
    "KeyName" : {
      "Description" : "KeyName",
      "Type" : "String",
      "Default" : "test"
    }
   },
  "Resources": {
    "WikiServerOne": {
      "Type": "AWS::EC2::Instance",
      "Properties": {
        "ImageId": "F17-x86_64-gold",
        "InstanceType"   : "m1.large",
        "KeyName"        : "test",
        "UserData"       : "some data"
      }
    },
    "LoadBalancer" : {
      "Type" : "AWS::ElasticLoadBalancing::LoadBalancer",
      "Properties" : {
        "AvailabilityZones" : ["nova"],
        "Instances" : [{"Ref": "WikiServerOne"}],
        "Listeners" : [ {
          "LoadBalancerPort" : "80",
          "InstancePort" : "80",
          "Protocol" : "HTTP"
        }]
      }
    }
  }
}
'''

lb_template_nokey = '''
{
  "AWSTemplateFormatVersion": "2010-09-09",
  "Description": "LB Template",
  "Resources": {
    "WikiServerOne": {
      "Type": "AWS::EC2::Instance",
      "Properties": {
        "ImageId": "F17-x86_64-gold",
        "InstanceType"   : "m1.large",
        "UserData"       : "some data"
      }
    },
    "LoadBalancer" : {
      "Type" : "AWS::ElasticLoadBalancing::LoadBalancer",
      "Properties" : {
        "AvailabilityZones" : ["nova"],
        "Instances" : [{"Ref": "WikiServerOne"}],
        "Listeners" : [ {
          "LoadBalancerPort" : "80",
          "InstancePort" : "80",
          "Protocol" : "HTTP"
        }]
      }
    }
  }
}
'''


class LoadBalancerTest(common.HeatTestCase):
    def setUp(self):
        super(LoadBalancerTest, self).setUp()
        self.fc = fakes_v1_1.FakeClient()
        self.m.StubOutWithMock(nova.NovaClientPlugin, '_create')
        self.m.StubOutWithMock(self.fc.servers, 'create')
        self.m.StubOutWithMock(resource.Resource, 'metadata_set')
        self.stub_keystoneclient(username='test_stack.CfnLBUser')

        cfg.CONF.set_default('heat_waitcondition_server_url',
                             'http://server.test:8000/v1/waitcondition')

    def create_loadbalancer(self, t, stack, resource_name):
        resource_defns = stack.t.resource_definitions(stack)
        rsrc = lb.LoadBalancer(resource_name,
                               resource_defns[resource_name],
                               stack)
        self.assertIsNone(rsrc.validate())
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        return rsrc

    def _mock_get_image_id_success(self, imageId_input, imageId):
        self.m.StubOutWithMock(glance.GlanceClientPlugin, 'get_image_id')
        glance.GlanceClientPlugin.get_image_id(
            imageId_input).MultipleTimes().AndReturn(imageId)

    def _create_stubs(self, key_name='test', stub_meta=True):
        server_name = utils.PhysName(
            utils.PhysName('test_stack', 'LoadBalancer'),
            'LB_instance',
            limit=instance.Instance.physical_resource_name_limit)
        nova.NovaClientPlugin._create().AndReturn(self.fc)
        self.fc.servers.create(
            flavor=2, image=746, key_name=key_name,
            meta=None, nics=None, name=server_name,
            scheduler_hints=None, userdata=mox.IgnoreArg(),
            security_groups=None, availability_zone=None,
            block_device_mapping=None).AndReturn(
                self.fc.servers.list()[1])
        if stub_meta:
            resource.Resource.metadata_set(mox.IgnoreArg()).AndReturn(None)

        self.m.StubOutWithMock(aws_wch.WaitConditionHandle, 'get_status')
        aws_wch.WaitConditionHandle.get_status().AndReturn(['SUCCESS'])

    def test_loadbalancer(self):
        self._mock_get_image_id_success(
            u'Fedora-Cloud-Base-20141203-21.x86_64', 746)
        self._create_stubs()
        self.m.ReplayAll()

        t = template_format.parse(lb_template)
        s = utils.parse_stack(t)
        s.store()

        rsrc = self.create_loadbalancer(t, s, 'LoadBalancer')

        id_list = []
        resource_defns = s.t.resource_definitions(s)
        for inst_name in ['WikiServerOne1', 'WikiServerOne2']:
            inst = instance.Instance(inst_name,
                                     resource_defns['WikiServerOne'],
                                     s)
            id_list.append(inst.FnGetRefId())

        prop_diff = {'Instances': id_list}
        props = copy.copy(rsrc.properties.data)
        props.update(prop_diff)
        update_defn = rsrc_defn.ResourceDefinition(rsrc.name, rsrc.type(),
                                                   props)
        rsrc.handle_update(update_defn, {}, prop_diff)

        self.assertIsNone(rsrc.handle_update(rsrc.t, {}, {}))

        self.m.VerifyAll()

    def test_loadbalancer_nokey(self):
        self._mock_get_image_id_success(
            u'Fedora-Cloud-Base-20141203-21.x86_64', 746)
        self._create_stubs(key_name=None, stub_meta=False)

        self.m.ReplayAll()

        t = template_format.parse(lb_template_nokey)
        s = utils.parse_stack(t)
        s.store()

        rsrc = self.create_loadbalancer(t, s, 'LoadBalancer')
        self.assertEqual('LoadBalancer', rsrc.name)
        self.m.VerifyAll()

    def test_loadbalancer_validate_hchk_good(self):
        rsrc = self.setup_loadbalancer()
        rsrc._parse_nested_stack = mock.Mock()
        hc = {
            'Target': 'HTTP:80/',
            'HealthyThreshold': '3',
            'UnhealthyThreshold': '5',
            'Interval': '30',
            'Timeout': '5'}
        rsrc.t['Properties']['HealthCheck'] = hc
        self.assertIsNone(rsrc.validate())

    def test_loadbalancer_validate_hchk_int_gt_tmo(self):
        rsrc = self.setup_loadbalancer()
        rsrc._parse_nested_stack = mock.Mock()
        hc = {
            'Target': 'HTTP:80/',
            'HealthyThreshold': '3',
            'UnhealthyThreshold': '5',
            'Interval': '30',
            'Timeout': '35'}
        rsrc.t['Properties']['HealthCheck'] = hc
        self.assertEqual(
            {'Error': 'Interval must be larger than Timeout'},
            rsrc.validate())

    def test_loadbalancer_validate_badtemplate(self):
        cfg.CONF.set_override('loadbalancer_template', '/a/noexist/x.y')
        rsrc = self.setup_loadbalancer()
        self.assertRaises(exception.StackValidationFailed, rsrc.validate)

    def setup_loadbalancer(self, include_keyname=True):
        template = template_format.parse(lb_template)
        if not include_keyname:
            del template['Parameters']['KeyName']
        stack = utils.parse_stack(template)

        resource_name = 'LoadBalancer'
        lb_defn = stack.t.resource_definitions(stack)[resource_name]
        return lb.LoadBalancer(resource_name, lb_defn, stack)

    def test_loadbalancer_refid(self):
        rsrc = self.setup_loadbalancer()
        rsrc.resource_id = mock.Mock(return_value='not-this')
        self.assertEqual('LoadBalancer', rsrc.FnGetRefId())

    def test_loadbalancer_attr_dnsname(self):
        rsrc = self.setup_loadbalancer()
        rsrc.get_output = mock.Mock(return_value='1.3.5.7')
        self.assertEqual('1.3.5.7', rsrc.FnGetAtt('DNSName'))
        rsrc.get_output.assert_called_once_with('PublicIp')

    def test_loadbalancer_attr_not_supported(self):
        rsrc = self.setup_loadbalancer()
        for attr in ['CanonicalHostedZoneName',
                     'CanonicalHostedZoneNameID',
                     'SourceSecurityGroup.GroupName',
                     'SourceSecurityGroup.OwnerAlias']:
            self.assertEqual('', rsrc.FnGetAtt(attr))

    def test_loadbalancer_attr_invalid(self):
        rsrc = self.setup_loadbalancer()
        self.assertRaises(exception.InvalidTemplateAttribute,
                          rsrc.FnGetAtt, 'Foo')

    def test_child_params_without_key_name(self):
        rsrc = self.setup_loadbalancer(False)
        self.assertEqual({}, rsrc.child_params())

    def test_child_params_with_key_name(self):
        rsrc = self.setup_loadbalancer()
        params = rsrc.child_params()
        self.assertEqual('test', params['KeyName'])

    def test_child_template_without_key_name(self):
        rsrc = self.setup_loadbalancer(False)
        parsed_template = {
            'Resources': {'LB_instance': {'Properties': {'KeyName': 'foo'}}},
            'Parameters': {'KeyName': 'foo'}
        }
        rsrc.get_parsed_template = mock.Mock(return_value=parsed_template)

        tmpl = rsrc.child_template()
        self.assertNotIn('KeyName', tmpl['Parameters'])
        self.assertNotIn('KeyName',
                         tmpl['Resources']['LB_instance']['Properties'])

    def test_child_template_with_key_name(self):
        rsrc = self.setup_loadbalancer()
        rsrc.get_parsed_template = mock.Mock(return_value='foo')

        self.assertEqual('foo', rsrc.child_template())


class HaProxyConfigTest(common.HeatTestCase):
    def setUp(self):
        super(HaProxyConfigTest, self).setUp()
        stack = utils.parse_stack(template_format.parse(lb_template))
        resource_name = 'LoadBalancer'
        lb_defn = stack.t.resource_definitions(stack)[resource_name]
        self.lb = lb.LoadBalancer(resource_name, lb_defn, stack)
        self.lb.client_plugin = mock.Mock()

    def _mock_props(self, props):
        def get_props(name):
            return props[name]

        self.lb.properties = mock.MagicMock()
        self.lb.properties.__getitem__.side_effect = get_props

    def test_combined(self):
        self.lb._haproxy_config_global = mock.Mock(return_value='one,')
        self.lb._haproxy_config_frontend = mock.Mock(return_value='two,')
        self.lb._haproxy_config_backend = mock.Mock(return_value='three,')
        self.lb._haproxy_config_servers = mock.Mock(return_value='four')
        actual = self.lb._haproxy_config([3, 5])
        self.assertEqual('one,two,three,four\n', actual)

        self.lb._haproxy_config_global.assert_called_once_with()
        self.lb._haproxy_config_frontend.assert_called_once_with()
        self.lb._haproxy_config_backend.assert_called_once_with()
        self.lb._haproxy_config_servers.assert_called_once_with([3, 5])

    def test_global(self):
        exp = '''
global
    daemon
    maxconn 256
    stats socket /tmp/.haproxy-stats

defaults
    mode http
    timeout connect 5000ms
    timeout client 50000ms
    timeout server 50000ms
'''
        actual = self.lb._haproxy_config_global()
        self.assertEqual(exp, actual)

    def test_frontend(self):
        props = {'HealthCheck': {},
                 'Listeners': [{'LoadBalancerPort': 4014}]}
        self._mock_props(props)

        exp = '''
frontend http
    bind *:4014
    default_backend servers
'''
        actual = self.lb._haproxy_config_frontend()
        self.assertEqual(exp, actual)

    def test_backend_with_timeout(self):
        props = {'HealthCheck': {'Timeout': 43}}
        self._mock_props(props)

        actual = self.lb._haproxy_config_backend()
        exp = '''
backend servers
    balance roundrobin
    option http-server-close
    option forwardfor
    option httpchk
    timeout check 43s
'''
        self.assertEqual(exp, actual)

    def test_backend_no_timeout(self):
        self._mock_props({'HealthCheck': None})
        be = self.lb._haproxy_config_backend()

        exp = '''
backend servers
    balance roundrobin
    option http-server-close
    option forwardfor
    option httpchk

'''
        self.assertEqual(exp, be)

    def test_servers_none(self):
        props = {'HealthCheck': {},
                 'Listeners': [{'InstancePort': 1234}]}
        self._mock_props(props)
        actual = self.lb._haproxy_config_servers([])
        exp = ''
        self.assertEqual(exp, actual)

    def test_servers_no_check(self):
        props = {'HealthCheck': {},
                 'Listeners': [{'InstancePort': 4511}]}
        self._mock_props(props)

        def fake_to_ipaddr(inst):
            return '192.168.1.%s' % inst

        to_ip = self.lb.client_plugin.return_value.server_to_ipaddress
        to_ip.side_effect = fake_to_ipaddr

        actual = self.lb._haproxy_config_servers(range(1, 3))
        exp = '''
    server server1 192.168.1.1:4511
    server server2 192.168.1.2:4511'''
        self.assertEqual(exp.replace('\n', '', 1), actual)

    def test_servers_servers_and_check(self):
        props = {'HealthCheck': {'HealthyThreshold': 1,
                                 'Interval': 2,
                                 'Target': 'HTTP:80/',
                                 'Timeout': 45,
                                 'UnhealthyThreshold': 5
                                 },
                 'Listeners': [{'InstancePort': 1234}]}
        self._mock_props(props)

        def fake_to_ipaddr(inst):
            return '192.168.1.%s' % inst

        to_ip = self.lb.client_plugin.return_value.server_to_ipaddress
        to_ip.side_effect = fake_to_ipaddr

        actual = self.lb._haproxy_config_servers(range(1, 3))
        exp = '''
    server server1 192.168.1.1:1234 check inter 2s fall 5 rise 1
    server server2 192.168.1.2:1234 check inter 2s fall 5 rise 1'''
        self.assertEqual(exp.replace('\n', '', 1), actual)

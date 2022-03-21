# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import collections
import json
import os.path
import re
import markupsafe

from lxml import etree, html
from lxml.builder import E
from copy import deepcopy
from textwrap import dedent

from odoo.modules import get_module_resource
from odoo.tests.common import TransactionCase
from odoo.addons.base.models.ir_qweb import QWebException, render
from odoo.tools import misc, mute_logger
from odoo.tools.json import scriptsafe as json_scriptsafe
from odoo.exceptions import MissingError

unsafe_eval = eval


class TestQWebTField(TransactionCase):
    def setUp(self):
        super(TestQWebTField, self).setUp()
        self.env_branding = self.env(context={'inherit_branding': True})
        self.engine = self.env_branding['ir.qweb']

    def test_trivial(self):
        field = etree.Element('span', {'t-field': 'company.name'})
        company = self.env['res.company'].create({'name': "My Test Company"})

        result = self.engine._render(field, {'company': company})
        self.assertEqual(
            etree.fromstring(result),
            etree.fromstring('<span data-oe-model="res.company" data-oe-id="%d" '
                  'data-oe-field="name" data-oe-type="char" '
                  'data-oe-expression="company.name">%s</span>' % (
                company.id,
                "My Test Company",
            )),
        )

    def test_i18n(self):
        field = etree.Element('span', {'t-field': 'company.name'})
        s = "Testing «ταБЬℓσ»: 1<2 & 4+1>3, now 20% off!"
        company = self.env['res.company'].create({'name': s})

        result = self.engine._render(field, {'company': company})
        self.assertEqual(
            etree.fromstring(result),
            etree.fromstring('<span data-oe-model="res.company" data-oe-id="%d" '
                  'data-oe-field="name" data-oe-type="char" '
                  'data-oe-expression="company.name">%s</span>' % (
                company.id,
                misc.html_escape(s),
            )),
        )

    def test_reject_crummy_tags(self):
        field = etree.Element('td', {'t-field': 'company.name'})

        with self.assertRaisesRegex(QWebException, r'QWeb widgets do not work correctly'):
            self.engine._render(field, {'company': None})

    def test_reject_t_tag(self):
        field = etree.Element('t', {'t-field': 'company.name'})

        with self.assertRaisesRegex(QWebException, r't-field can not be used on a t element'):
            self.engine._render(field, {'company': None})

    def test_render_t_options(self):
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy"><root><span t-esc="5" t-options="{'widget': 'char'}" t-options-widget="'float'" t-options-precision="4"/></root></t>
            """
        })
        text = etree.fromstring(view1._render()).find('span').text
        self.assertEqual(text, '5.0000')

    def test_xss_breakout(self):
        view = self.env['ir.ui.view'].create({
            'name': 'dummy', 'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <root>
                        <script type="application/javascript">
                            var s = <t t-esc="json.dumps({'key': malicious})"/>;
                        </script>
                    </root>
                </t>
            """
        })
        rendered = view._render({'malicious': '1</script><script>alert("pwned")</script><script>'})
        self.assertIn('alert', rendered, "%r doesn't seem to be rendered" % rendered)
        doc = etree.fromstring(rendered)
        self.assertEqual(len(doc.xpath('//script')), 1)

class TestQWebNS(TransactionCase):
    def test_render_static_xml_with_namespace(self):
        """ Test the rendering on a namespaced view with no static content. The resulting string should be untouched.
        """
        expected_result = """
            <root>
                <h:table xmlns:h="http://www.example.org/table">
                    <h:tr>
                        <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                        <h:td>Bananas</h:td>
                    </h:tr>
                </h:table>
                <f:table xmlns:f="http://www.example.org/furniture">
                    <f:width>80</f:width>
                </f:table>
            </root>
        """

        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">%s</t>
            """ % expected_result
        })

        self.assertEqual(etree.fromstring(view1._render()), etree.fromstring(expected_result))

    def test_render_static_xml_with_namespace_2(self):
        """ Test the rendering on a namespaced view with no static content. The resulting string should be untouched.
        """
        expected_result = """
            <html xmlns="http://www.w3.org/HTML/1998/html4" xmlns:xdc="http://www.xml.com/books">
                <head>
                    <title>Book Review</title>
                </head>
                <body>
                    <xdc:bookreview>
                        <xdc:title>XML: A Primer</xdc:title>
                        <table>
                            <tr align="center">
                                <td>Author</td><td>Price</td>
                                <td>Pages</td><td>Date</td>
                            </tr>
                            <tr align="left">
                                <td><xdc:author>Simon St. Laurent</xdc:author></td>
                                <td><xdc:price>31.98</xdc:price></td>
                                <td><xdc:pages>352</xdc:pages></td>
                                <td><xdc:date>1998/01</xdc:date></td>
                            </tr>
                        </table>
                    </xdc:bookreview>
                </body>
            </html>
        """

        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">%s</t>
            """ % expected_result
        })

        self.assertEqual(etree.fromstring(view1._render()), etree.fromstring(expected_result))

    def test_render_static_xml_with_useless_distributed_namespace(self):
        """ Test that redundant namespaces are stripped upon rendering.
        """
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <root>
                        <h:table xmlns:h="http://www.example.org/table">
                            <h:tr xmlns:h="http://www.example.org/table">
                                <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                                <h:td xmlns:h="http://www.example.org/table">Bananas</h:td>
                            </h:tr>
                        </h:table>
                    </root>
                </t>
            """
        })

        expected_result = etree.fromstring("""
            <root>
                <h:table xmlns:h="http://www.example.org/table">
                    <h:tr>
                        <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                        <h:td>Bananas</h:td>
                    </h:tr>
                </h:table>
            </root>
        """)

        self.assertEqual(etree.fromstring(view1._render()), expected_result)

    def test_render_static_xml_with_namespace_3(self):
        expected_result = """
            <cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.sat.gob.mx/cfd/3 http://www.sat.gob.mx/sitio_internet/cfd/3/cfdv32.xsd"></cfdi:Comprobante>
        """

        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">%s</t>
            """ % expected_result
        })

        self.assertEqual(etree.fromstring(view1._render()), etree.fromstring(expected_result))

    def test_render_static_xml_with_namespace_dynamic(self):
        """ Test the rendering on a namespaced view with dynamic URI (need default namespace uri).
        """
        tempate = """
            <root xmlns:h="https://default.namespace.url/h">
                <h:table t-att="{'xmlns:h': h1}">
                    <h:tr>
                        <h:td t-att="{'xmlns:h': h2}">Apples</h:td>
                        <h:td>Bananas</h:td>
                    </h:tr>
                </h:table>
            </root>
        """
        expected_result = """
            <root xmlns:h="https://default.namespace.url/h">
                <h:table xmlns:h="%(h1)s">
                    <h:tr>
                        <h:td xmlns:h="%(h2)s">Apples</h:td>
                        <h:td>Bananas</h:td>
                    </h:tr>
                </h:table>
            </root>
        """

        values = dict(h1="http://www.example.org/table", h2="http://www.w3.org/TD/html4/")

        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">%s</t>
            """ % tempate
        })

        rendering = view1._render(values, engine='ir.qweb')

        self.assertEqual(etree.fromstring(rendering), etree.fromstring(expected_result % values))

    def test_render_static_xml_with_namespace_dynamic_2(self):
        """ Test the rendering on a namespaced view with dynamic URI (need default namespace uri).
        Default URIs must be differents.
        """
        tempate = """
            <root xmlns:f="https://default.namespace.url/f" xmlns:h="https://default.namespace.url/h" >
                <h:table t-att="{'xmlns:h': h1}">
                    <h:tr>
                        <h:td t-att="{'xmlns:h': h2}">Apples</h:td>
                        <h:td>Bananas</h:td>
                    </h:tr>
                </h:table>
                <f:table t-att="{'xmlns:f': f}">
                    <f:width>80</f:width>
                </f:table>
            </root>
        """
        expected_result = """
            <root xmlns:f="https://default.namespace.url/f" xmlns:h="https://default.namespace.url/h">
                <h:table xmlns:h="%(h1)s">
                    <h:tr>
                        <h:td xmlns:h="%(h2)s">Apples</h:td>
                        <h:td>Bananas</h:td>
                    </h:tr>
                </h:table>
                <f:table xmlns:f="%(f)s">
                    <f:width>80</f:width>
                </f:table>
            </root>
        """

        values = dict(h1="http://www.example.org/table", h2="http://www.w3.org/TD/html4/", f="http://www.example.org/furniture")

        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">%s</t>
            """ % tempate
        })

        rendering = view1._render(values, engine='ir.qweb')

        self.assertEqual(etree.fromstring(rendering), etree.fromstring(expected_result % values))

    def test_render_dynamic_xml_with_namespace_t_esc(self):
        """ Test that rendering a template containing a node having both an ns declaration and a t-esc attribute correctly
        handles the t-esc attribute and keep the ns declaration.
        """
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" t-esc="'test'"/>
                </t>
            """
        })

        expected_result = etree.fromstring("""<Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">test</Invoice>""")

        self.assertEqual(etree.fromstring(view1._render()), expected_result)

    def test_render_dynamic_xml_with_namespace_t_esc_with_useless_distributed_namespace(self):
        """ Test that rendering a template containing a node having both an ns declaration and a t-esc attribute correctly
        handles the t-esc attribute and keep the ns declaration, and distribute correctly the ns declaration to its children.
        """
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" t-attf-test="test">
                        <cac:Test xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2">blabla</cac:Test>
                    </Invoice>
                </t>
            """
        })

        expected_result = etree.fromstring("""
            <Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" test="test">
                <cac:Test>blabla</cac:Test>
            </Invoice>
        """)

        self.assertEqual(etree.fromstring(view1._render()), expected_result)

    def test_render_dynamic_xml_with_namespace_t_attf(self):
        """ Test that rendering a template containing a node having both an ns declaration and a t-attf attribute correctly
        handles the t-attf attribute and keep the ns declaration.
        """
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <root>
                        <h:table xmlns:h="http://www.example.org/table">
                            <h:tr>
                                <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                                <h:td>Bananas</h:td>
                            </h:tr>
                        </h:table>
                        <f:table xmlns:f="http://www.example.org/furniture">
                            <f:width t-attf-test="1">80</f:width>
                        </f:table>
                    </root>
                </t>
            """
        })

        expected_result = etree.fromstring("""
            <root>
                <h:table xmlns:h="http://www.example.org/table">
                    <h:tr>
                        <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                        <h:td>Bananas</h:td>
                    </h:tr>
                </h:table>
                <f:table xmlns:f="http://www.example.org/furniture">
                    <f:width test="1">80</f:width>
                </f:table>
            </root>
        """)

        self.assertEqual(etree.fromstring(view1._render()), expected_result)

    def test_render_dynamic_xml_with_namespace_t_attf_with_useless_distributed_namespace(self):
        """ Test that rendering a template containing a node having both an ns declaration and a t-attf attribute correctly
        handles the t-attf attribute and that redundant namespaces are stripped upon rendering.
        """
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                <root>
                    <h:table xmlns:h="http://www.example.org/table">
                        <h:tr>
                            <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                            <h:td>Bananas</h:td>
                        </h:tr>
                    </h:table>
                    <f:table xmlns:f="http://www.example.org/furniture">
                        <f:width xmlns:f="http://www.example.org/furniture" t-attf-test="1">80</f:width>
                    </f:table>
                </root>

                </t>
            """
        })

        expected_result = etree.fromstring("""
                <root>
                    <h:table xmlns:h="http://www.example.org/table">
                        <h:tr>
                            <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                            <h:td>Bananas</h:td>
                        </h:tr>
                    </h:table>
                    <f:table xmlns:f="http://www.example.org/furniture">
                        <f:width test="1">80</f:width>
                    </f:table>
                </root>

        """)

        self.assertEqual(etree.fromstring(view1._render()), expected_result)

    def test_render_dynamic_xml_with_namespace_2(self):
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">
                        <cbc:UBLVersionID t-esc="version_id"/>
                        <t t-foreach="[1, 2, 3, 4]" t-as="value">
                            Oasis <cac:Test t-esc="value"/>
                        </t>
                    </Invoice>
                </t>
            """
        })

        expected_result = etree.fromstring("""
            <Invoice xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">
                <cbc:UBLVersionID>1.0</cbc:UBLVersionID>

                    Oasis <cac:Test>1</cac:Test>

                    Oasis <cac:Test>2</cac:Test>

                    Oasis <cac:Test>3</cac:Test>

                    Oasis <cac:Test>4</cac:Test>

            </Invoice>
        """)

        self.assertEqual(etree.fromstring(view1._render({'version_id': 1.0})), expected_result)

    def test_render_static_xml_with_namespaced_attributes(self):
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.sat.gob.mx/cfd/3 http://www.sat.gob.mx/sitio_internet/cfd/3/cfdv32.xsd">abc</cfdi:Comprobante>
                </t>
            """
        })

        expected_result = etree.fromstring("""<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.sat.gob.mx/cfd/3 http://www.sat.gob.mx/sitio_internet/cfd/3/cfdv32.xsd">abc</cfdi:Comprobante>""")

        self.assertEqual(etree.fromstring(view1._render()), expected_result)

    def test_render_dynamic_xml_with_namespaced_attributes(self):
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.sat.gob.mx/cfd/3 http://www.sat.gob.mx/sitio_internet/cfd/3/cfdv32.xsd" t-esc="'abc'"/>
                </t>
            """
        })

        expected_result = etree.fromstring("""<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.sat.gob.mx/cfd/3 http://www.sat.gob.mx/sitio_internet/cfd/3/cfdv32.xsd">abc</cfdi:Comprobante>""")

        self.assertEqual(etree.fromstring(view1._render()), expected_result)

    def test_render_static_xml_with_t_call(self):
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <cac:fruit xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
                               xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
                        <cac:table>
                            <cbc:td>Appel</cbc:td>
                            <cbc:td>Pineappel</cbc:td>
                        </cac:table>
                    </cac:fruit>
                </t>
            """
        })
        self.env.cr.execute("INSERT INTO ir_model_data(name, model, res_id, module)"
                            "VALUES ('dummy', 'ir.ui.view', %s, 'base')", [view1.id])

        # view2 will t-call view1
        view2 = self.env['ir.ui.view'].create({
            'name': "dummy2",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy2">
                    <root xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2" xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
                        <cac:line t-foreach="[1, 2]" t-as="i" t-call="base.dummy"/>
                    </root>
                </t>
            """
        })

        result = view2._render()
        result_etree = etree.fromstring(result)

        # check that the root tag has all its xmlns
        expected_ns = {
            (None, 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'),
            ('cac', 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'),
            ('cbc', 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'),
        }
        self.assertEqual(set(result_etree.nsmap.items()), expected_ns)

        # check that the t-call did its work
        cac_lines = result_etree.findall('.//cac:line', namespaces={'cac': 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'})
        self.assertEqual(len(cac_lines), 2)
        self.assertEqual(result.count('Appel'), 2)

        # check that the t-call dit not output again the xmlns declaration
        self.assertEqual(result.count('xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"'), 1)

    def test_render_static_xml_with_extension(self):
        """ Test the extension of a view by an xpath expression on a ns prefixed element.
        """
        # primary view
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <root>
                        <h:table xmlns:h="http://www.example.org/table">
                            <h:tr>
                                <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                                <h:td>Bananas</h:td>
                            </h:tr>
                        </h:table>
                    </root>
                </t>
            """
        })
        # extension patching the primary view
        view2 = self.env['ir.ui.view'].create({
            'name': "dummy_ext",
            'type': 'qweb',
            'inherit_id': view1.id,
            'arch': """
                <xpath expr="//{http://www.example.org/table}table/{http://www.example.org/table}tr">
                        <h:td xmlns:h="http://www.example.org/table">Oranges</h:td>
                </xpath>
            """
        })

        expected_result = etree.fromstring("""
            <root>
                <h:table xmlns:h="http://www.example.org/table">
                    <h:tr>
                        <h:td xmlns:h="http://www.w3.org/TD/html4/">Apples</h:td>
                        <h:td>Bananas</h:td>
                        <h:td>Oranges</h:td>
                    </h:tr>
                </h:table>
            </root>
        """)

        self.assertEqual(
            etree.fromstring(view1.with_context(check_view_ids=[view1.id, view2.id])._render()),
            expected_result
        )

    def test_render_dynamic_xml_with_code_error(self):
        """ Test that, when rendering a template containing a namespaced node
            that evaluates code with errors, the proper exception is raised
        """
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <Invoice xmlns:od="http://odoo.com/od">
                        <od:name t-att-test="'a' + 1"/>
                    </Invoice>
                </t>
            """
        })

        try:
            "" + 0
        except TypeError as e:
            error_msg = e.args[0]

        with self.assertRaises(QWebException, msg=error_msg):
            view1._render()

class TestQWebBasic(TransactionCase):
    def test_compile_expr(self):
        tests = [
            #pylint: disable=C0326
            # source,                                   values,                         result
            ("1 +2+ 3",                                 {},                             6),
            ("(((1 +2+ 3)))",                           {},                             6),
            ("(1) +(2+ (3))",                           {},                             6),
            ("a == 5",                                  {'a': 5},                       True),
            ("{'a': True}",                             {},                             {'a': True}),
            ("object.count(1)",                         {'object': [1, 2, 1 ,1]},       3),
            ("dict(a=True)",                            {},                             {'a': True}),
            ("fn(a=11, b=22) or a",                     {'a': 1, 'fn': lambda a,b: 0},  1),
            ("fn(a=11, b=22) or a",                     {'a': 1, 'fn': lambda a,b: b},  22),
            ("(lambda a: a)(5)",                        {},                             5),
            ("(lambda a: a[0])([5])",                   {},                             5),
            ("(lambda test: len(test))('aaa')",         {},                             3),
            ("{'a': lambda a: a[0], 'b': 3}['a']([5])", {},                             5),
            ("list(map(lambda a: a[0], r))",            {'r': [(1,11), (2,22)]},        [1, 2]),
            ("z + (head or 'z')",                       {'z': 'a'},                     "az"),
            ("z + (head or 'z')",                       {'z': 'a', 'head': 'b'},        "ab"),
            ("{a:b for a, b in [(1,11), (2, 22)]}",     {},                             {1: 11, 2: 22}),
            ("any({x == 2 for x in [1,2,3]})",          {},                             True),
            ("any({x == 5 for x in [1,2,3]})",          {},                             False),
            ("{x:y for x,y in [('a', 11),('b', 22)]}",  {},                             {'a': 11, 'b': 22}),
            ("[(y,x) for x,y in [(1, 11),(2, 22)]]",    {},                             [(11, 1), (22, 2)]),
            ("(lambda a: a + 5)(x)",                    {'x': 10},                      15),
            ("(lambda a: a + x)(5)",                    {'x': 10},                      15),
            ("sum(x for x in range(4)) + ((x))",        {'x': 10},                      16),
            ("['test_' + x for x in ['a', 'b']]",       {},                             ['test_a', 'test_b']),
            ("""1 and 2 and 0
                or 9""",                                {},                             9),
        ]

        IrQweb = self.env['ir.qweb']
        for expr, q_values, result in tests:
            expr_namespace = IrQweb._compile_expr(expr)

            compiled = compile("""def test(values):\n  values['result'] = %s""" % expr_namespace, '<test>', 'exec')
            globals_dict = IrQweb._prepare_globals()
            values = {}
            unsafe_eval(compiled, globals_dict, values)
            test = values['test']

            test(q_values)
            q_result = dict(q_values, result=result)
            self.assertDictEqual(q_values, q_result, "Should compile: %s" % expr)

    def test_foreach_as_error_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="iter-list">
                <t t-foreach="[3, 2, 1]">
                    [<t t-esc="item_index"/>: <t t-esc="item"/> <t t-esc="item_value"/>]</t>
            </t>'''
        })

        with self.assertRaises(QWebException):
            t._render()

        try:
            t._render()
        except QWebException as e:
            error = str(e)
            self.assertIn("KeyError: 't-as'", error)
            self.assertIn('<t t-foreach="[3, 2, 1]"/>', error)

    def test_foreach_as_error_2(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="iter-list">
                <t t-foreach="[3, 2, 1]" t-as="">
                    [<t t-esc="item_index"/>: <t t-esc="item"/> <t t-esc="item_value"/>]</t>
            </t>'''
        })

        with self.assertRaises(QWebException):
            t._render()

        try:
            t._render()
        except QWebException as e:
            error = str(e)
            self.assertIn("KeyError: 't-as'", error)
            self.assertIn('<t t-foreach="[3, 2, 1]" t-as=""/>', error)

    def test_foreach_as_error_3(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="iter-list">
                <t t-foreach="[3, 2, 1]" t-as="b-2">
                    [<t t-esc="item_index"/>: <t t-esc="item"/> <t t-esc="item_value"/>]</t>
            </t>'''
        })

        with self.assertRaises(QWebException):
            t._render()

        try:
            t._render()
        except QWebException as e:
            error = str(e)
            self.assertIn("The varname 'b-2' can only contain alphanumeric characters and underscores", error)
            self.assertIn('<t t-foreach="[3, 2, 1]" t-as="b-2"/>', error)

    def test_compile_expr_security(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-escaping">
                <div>
                    <t t-set="o" t-value="(lambda a=open: a)()"/>
                    <t t-out="o('/etc/passwd').read()"/>
                </div>
            </t>'''
        })
        values = {'other': 'any value'}
        with self.assertRaises(Exception): # NotImplementedError for 'lambda a=open' and Undefined value 'open'.
            self.env['ir.qweb']._render(t.id, values)

    def test_foreach_iter_list(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="iter-list">
                <t t-foreach="[3, 2, 1]" t-as="item">
                    [<t t-esc="item_index"/>: <t t-esc="item"/> <t t-esc="item_value"/>]</t>
            </t>'''
        })
        result = """
                    [0: 3 3]
                    [1: 2 2]
                    [2: 1 1]
        """

        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_foreach_iter_dict(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="iter-dict">
                <t t-foreach="{'a': 3, 'b': 2, 'c': 1}" t-as="item">
                    [<t t-esc="item_index"/>: <t t-esc="item"/> <t t-esc="item_value"/>]</t>
            </t>'''
        })
        result = """
                    [0: a 3]
                    [1: b 2]
                    [2: c 1]
        """

        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_att_escaping_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-escaping">
                <div t-att-bibi="json.dumps(bibi)">1</div>
                <div t-att-toto="toto">2</div>
            </t>'''
        })
        result = """
                <div bibi="{&#34;a&#34;: &#34;string&#34;, &#34;b&#34;: 1}">1</div>
                <div toto="a&#39;b&#34;c">2</div>
            """
        values = {'json': json_scriptsafe, 'bibi': dict(a='string', b=1), 'toto': "a'b\"c"}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_att_escaping_2(self):

        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-escaping">
                <t t-set="abc"> <t t-if="add_abc"><t t-out="add_abc"/> <span a="b"> | </span></t><t t-out="efg"/> </t>
                <div t-att-abc="abc">123</div>
            </t>'''
        })
        result = """
                <div abc=" &amp;#34;yes&amp;#34; &lt;span a=&#34;b&#34;&gt; | &lt;/span&gt;-efg- ">123</div>
            """
        values = {'add_abc': '"yes"', 'efg': '-efg-'}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_attf_escaping_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-escaping">
                <div t-attf-bibi="a, b &gt; c &gt; #{d}">1</div>
            </t>'''
        })
        result = """
                <div bibi="a, b &gt; c &gt; a&#39; &gt; b&#34;c">1</div>
            """
        values = {'d': "a' > b\"c"}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_attf_escaping_2(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-escaping">
                <a t-attf-href="/link/#{ url }/#{other and 'sub'}">link</a>
                <a t-attf-href="/link/#{ url }/#{(not other) and 'sub'}">link2</a>
            </t>'''
        })
        result = """
                <a href="/link/odoo/sub">link</a>
                <a href="/link/odoo/">link2</a>
            """
        values = {'url': 'odoo', 'other': True}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_attf_escaping_3(self):

        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-escaping">
                <div t-attf-abc="abc #{val} { other }">123</div>
            </t>'''
        })
        result = """
                <div abc="abc &#34;yes&#34; { other }">123</div>
            """
        values = {'val': '"yes"'}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_att_no_propagation_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="bibi">
                <div t-foreach="[1, 2]" t-as="v" class="toto"/>
                <t class="remove_me" t-set="data">a</t>
                <div t-out="data"/>
            </t>'''
        })
        result = """
                <div class="toto"></div><div class="toto"></div>
                <div>a</div>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_att_no_propagation_2(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="bibi">
                <section>
                    <div t-foreach="[1, 2]" t-as="v">
                        <span t-att-test="v" t-esc="v_index"/>
                    </div>
                    <div t-foreach="[1, 2]" t-as="v" class="o">
                        <span t-att-test="v" t-esc="v_index"/>
                    </div>
                </section>
            </t>'''
        })
        result = """
                <section>
                    <div>
                        <span test="1">0</span>
                    </div>
                    <div>
                        <span test="2">1</span>
                    </div>
                    <div class="o">
                        <span test="1">0</span>
                    </div>
                    <div class="o">
                        <span test="2">1</span>
                    </div>
                </section>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(etree.fromstring(rendered), etree.fromstring(result))

    def test_set_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set="a" t-value="'abc %s' % 1"/>
                <div t-out="a"/>
            </t>'''
        })
        result = """
                <div>abc 1</div>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_set_2(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set="a" t-valuef="abc {{1}}"/>
                <div t-out="a"/>
            </t>'''
        })
        result = """
                <div>abc 1</div>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_set_3(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set-a="'abc %s' % 1"/>
                <div t-out="a"/>
            </t>'''
        })
        result = """
                <div>abc 1</div>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_set_4(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-setf-a="abc {{1}}"/>
                <div t-out="a"/>
            </t>'''
        })
        result = """
                <div>abc 1</div>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_set_5(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set='{"a": "abc %s" % 1,
                    "b": 2}'/>
                <div t-out="a"/>
            </t>'''
        })
        result = """
                <div>abc 1</div>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_set_body_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set="abc"> <span a="b"> [%s] </span> </t>
                <div t-att-abc="abc % add_abc">123</div>
            </t>'''
        })
        result = """
                <div abc=" &lt;span a=&#34;b&#34;&gt; [&amp;#34;yes&amp;#34;] &lt;/span&gt; ">123</div>
            """
        values = {'add_abc': '"yes"'}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_set_body_2(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set="abc"> <span a="b"> toto </span> </t>
                <div t-att-abc="'[%s]' % abc">123</div>
                <div class="a1" t-out="abc"/>
                <div class="a2" t-out="'[%s]' % abc"/>
            </t>'''
        })
        result = """
                <div abc="[ &lt;span a=&#34;b&#34;&gt; toto &lt;/span&gt; ]">123</div>
                <div class="a1"> <span a="b"> toto </span> </div>
                <div class="a2">[ &lt;span a=&#34;b&#34;&gt; toto &lt;/span&gt; ]</div>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_set_error_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set="" t-value="1"/>
            </t>'''
        })

        with self.assertRaises(QWebException):
            t._render()

        try:
            t._render()
        except QWebException as e:
            error = str(e)
            self.assertIn("KeyError: 't-set'", error)
            self.assertIn('<t t-set="" t-value="1"/>', error)

    def test_set_error_2(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set="b-2" t-value="1"/>
            </t>'''
        })

        with self.assertRaises(QWebException):
            t._render()

        try:
            t._render()
        except QWebException as e:
            error = str(e)
            self.assertIn("The varname can only contain alphanumeric characters and underscores", error)
            self.assertIn('<t t-set="b-2" t-value="1"/>', error)

    def test_out(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="out-format"><div t-out="a">Default</div></t>'''
        })
        result = """<div>1</div>"""
        rendered = self.env['ir.qweb']._render(t.id, {'a': 1})
        self.assertEqual(rendered.strip(), result.strip())

    def test_out_format_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="out-format">
                <t t-set="final_message">Powered by %s%s</t>
                <div t-out="final_message % (a, b and ('-%s' % b) or '')"/>
            </t>'''
        })
        result = """
                <div>Powered by 1-2</div>
        """
        rendered = self.env['ir.qweb']._render(t.id, {'a': 1, 'b': 2})
        self.assertEqual(rendered.strip(), result.strip())

    def test_out_format_2(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="set">
                <t t-set="toto">Toto %s</t>
                <t t-set="abc"> <span a="b"> [%s , %s] </span> </t>
                <div t-out="(abc % (add_abc, toto)) % 5">123</div>
            </t>'''
        })
        result = """
                <div> <span a="b"> [&#34;yes&#34; , Toto 5] </span> </div>
            """
        values = {'add_abc': '"yes"'}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_out_format_3(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-set">
                <t t-set="toto">Toto %s</t>
                <t t-set="abc"> <span a="b"> a </span> </t>
                <div t-out="(toto + abc) % v">123</div>
            </t>'''
        })
        result = """
                <div>Toto &#34;yes&#34; <span a="b"> a </span> </div>
            """
        values = {'v': '"yes"'}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_out_format_4(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-set">
                <t t-set="abc"> <span a="b"> a </span> </t>
                <div t-out="(v + abc)">123</div>
            </t>'''
        })
        result = """
                <div>&#34;yes&#34; <span a="b"> a </span> </div>
            """
        values = {'v': '"yes"'}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_out_format_5(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-set">
                <t t-set="abc"> <span a="b"> a </span> </t>
                <div t-out="(abc + v)">123</div>
            </t>'''
        })
        result = """
                <div> <span a="b"> a </span> &#34;yes&#34;</div>
            """
        values = {'v': '"yes"'}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_out_format_6(self):
        # Use str method will use the string value. t-out will escape this str
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-set">
                <t t-set="abc"> <span a="b"> a </span> </t>
                <div t-out="(abc.strip() + v)">123</div>
            </t>'''
        })
        result = """
                <div><span a="b"> a </span>&#34;yes&#34;</div>
            """
        values = {'v': '"yes"'}
        rendered = self.env['ir.qweb']._render(t.id, values)
        self.assertEqual(rendered.strip(), result.strip())

    def test_out_escape_text(self):
        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy"><root><span t-out="text" t-options-widget="'text'"/></root></t>
            """
        })
        html = view1._render({'text': """a
        b <b>c</b>"""})
        self.assertEqual(html, """<root><span data-oe-type="text" data-oe-expression="text">a<br>
        b &lt;b&gt;c&lt;/b&gt;</span></root>""")

    def test_out_markup(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="esc-markup">
                <t t-set="content"><span>toto</span></t>
                <div t-out="content"/>
            </t>'''
        })
        result = """
                <div><span>toto</span></div>
        """
        rendered = self.env['ir.qweb']._render(t.id, {})
        self.assertEqual(rendered.strip(), result.strip())

    def test_out_default_value(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="out-default">
                <span rows="10" t-out="a">
                    DEFAULT
                    <t t-out="'Text'" />
                </span>
            </t>'''
        })
        result = """
                <span rows="10">Hello</span>
        """
        rendered = self.env['ir.qweb']._render(t.id, {'a': 'Hello'})
        self.assertEqual(str(rendered.strip()), result.strip())

        result = """
                <span rows="10">
                    DEFAULT
                    Text
                </span>
        """
        rendered = self.env['ir.qweb']._render(t.id, {})
        self.assertEqual(str(rendered.strip()), result.strip())

    def test_esc_markup(self):
        # t-esc is equal to t-out
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="esc-markup">
                <t t-set="content"><span>toto</span></t>
                <div t-esc="content"/>
            </t>'''
        })
        ref = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="esc-markup">
                <t t-set="content"><span>toto</span></t>
                <div t-out="content"/>
            </t>'''
        })
        rendered = self.env['ir.qweb']._render(t.id, {})
        result = self.env['ir.qweb']._render(ref.id, {})
        self.assertEqual(rendered.strip(), result.strip())

    def test_if_from_body(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="attr-set">
                <t t-set="abc"> <span a="b"> a </span> </t>
                <div t-if="abc">123</div>
                <div t-if="not abc">456</div>
            </t>'''
        })
        result = """
                <div>123</div>
            """
        rendered = self.env['ir.qweb']._render(t.id)
        self.assertEqual(rendered.strip(), result.strip())

    def test_error_message_1(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="test">
                <section>
                    <div t-esc="abc + def">
                        <span>content</span>
                    </div>
                </section>
            </t>'''
        })
        with self.assertRaises(QWebException):
            self.env['ir.qweb']._render(t.id)

        try:
            self.env['ir.qweb']._render(t.id)
        except QWebException as e:
            error = str(e)
            self.assertIn('<div t-esc="abc + def"/>', error)

    def test_error_message_2(self):
        t = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name="test">
                <section>
                    <div t-esc="abc + def + (">
                        <span>content</span>
                    </div>
                </section>
            </t>'''
        })
        with self.assertRaises(QWebException):
            self.env['ir.qweb']._render(t.id)

        try:
            self.env['ir.qweb']._render(t.id)
        except QWebException as e:
            error = str(e)
            self.assertIn('Can not compile expression', error)
            self.assertIn('<div t-esc="abc + def + ("/>', error)

    def test_error_message_3(self):
        # The format error tells the developer what to do.
        template = '''<section>
                    <div t-esc="1+2">
                        <span>content</span>
                    </div>
                </section>'''
        with self.assertRaises(ValueError):
            self.env['ir.qweb']._render(template)
        try:
            self.env['ir.qweb']._render(template)
        except ValueError as e:
            self.assertIn('Inline templates must be passed as `etree` documents', str(e))

        template = '''toto <t t-esc="content"/>'''
        with self.assertRaises(ValueError):
            self.env['ir.qweb']._render(template)
        try:
            self.env['ir.qweb']._render(template)
        except ValueError as e:
            self.assertIn('Inline templates must be passed as `etree` documents', str(e))

    def test_error_message_4(self):
        # Template record view not found.
        with self.assertRaises(MissingError):
            self.env['ir.qweb']._render(-999)
        try:
            self.env['ir.qweb']._render(-999)
        except MissingError as e:
            self.assertIn('Record does not exist or has been deleted.', str(e))

        with self.assertRaises(ValueError):
            self.env['ir.qweb']._render('not.wrong_template_xmlid')
        try:
            self.env['ir.qweb']._render('not.wrong_template_xmlid')
        except ValueError as e:
            self.assertIn('External ID not found in the system', str(e))

        with self.assertRaises(AssertionError):
            self.env['ir.qweb']._render(False)
        try:
            self.env['ir.qweb']._render(False)
        except AssertionError as e:
            self.assertIn('template is required', str(e))

    def test_call_set(self):
        view0 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <table>
                        <tr><td t-out="a"/></tr>
                        <t t-set="a">3</t>
                    </table>
                </t>
            """
        })
        self.env.cr.execute("INSERT INTO ir_model_data(name, model, res_id, module)"
                            "VALUES ('dummy', 'ir.ui.view', %s, 'base')", [view0.id])

        view1 = self.env['ir.ui.view'].create({
            'name': "other",
            'type': 'qweb',
            'arch': """
                <t t-name="base.other">
                    <div>
                        <t t-set="a">1</t>
                        <t t-set="b">1</t>
                        <t t-call="base.dummy">
                            <t t-set="b">2</t>
                        </t>
                        <span t-out="a"/>
                        <span t-out="b"/>
                    </div>
                </t>
            """
        })

        result = view1._render({})
        self.assertEqual(etree.fromstring(result), etree.fromstring("""
            <div>
                <table>
                    <tr><td>1</td></tr>
                </table>
                <span>1</span>
                <span>1</span>
            </div>
        """), 'render t-call use lexical scoping, t-call content use independant scoping')

    def test_call_arg(self):
        view0 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <table>
                        <tr><td t-out="a"/></tr>
                        <tr><td t-out="b"/></tr>
                    </table>
                </t>
            """
        })
        self.env.cr.execute("INSERT INTO ir_model_data(name, model, res_id, module)"
                            "VALUES ('dummy', 'ir.ui.view', %s, 'base')", [view0.id])

        view1 = self.env['ir.ui.view'].create({
            'name': "other",
            'type': 'qweb',
            'arch': """
                <t t-name="base.other">
                    <div>
                        <t t-call="base.dummy" t-set-a="1" t-set-b="2"/>
                    </div>
                </t>
            """
        })

        result = view1._render({})
        self.assertEqual(etree.fromstring(result), etree.fromstring("""
            <div>
                <table>
                    <tr><td>1</td></tr>
                    <tr><td>2</td></tr>
                </table>
            </div>
        """), 'render t-call with t-set attributes')

    def test_call_arg_error(self):
        view0 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': 'qweb',
            'arch': """
                <t t-name="base.dummy">
                    <table>
                        <tr><td t-out="a-2"/></tr>
                        <tr><td t-out="b-2"/></tr>
                    </table>
                </t>
            """
        })
        self.env.cr.execute("INSERT INTO ir_model_data(name, model, res_id, module)"
                            "VALUES ('dummy', 'ir.ui.view', %s, 'base')", [view0.id])

        view1 = self.env['ir.ui.view'].create({
            'name': "other",
            'type': 'qweb',
            'arch': """
                <t t-name="base.other">
                    <div>
                        <t t-call="base.dummy" t-set-a-2="1" t-set-b-2="2"/>
                    </div>
                </t>
            """
        })

        with self.assertRaises(QWebException):
            view1._render()

        try:
            view1._render()
        except QWebException as e:
            error = str(e)
            self.assertIn("The varname 'a-2' can only contain alphanumeric characters and underscores", error)
            self.assertIn('<t t-call="base.dummy" t-set-a-2="1" t-set-b-2="2"/>', error)

    def test_call_error(self):
        view1 = self.env['ir.ui.view'].create({
            'name': "other",
            'type': 'qweb',
            'arch': """
                <t t-name="base.other">
                    <div>
                        <t t-call="base.dummy"/>
                    </div>
                </t>
            """
        })

        with self.assertRaises(QWebException):
            view1._render()

        try:
            view1._render()
        except QWebException as e:
            error = str(e)
            self.assertIn('External ID not found in the system: base.dummy', error)
            self.assertIn('<t t-call="base.dummy"/>', error)

    def test_render_t_call_propagates_t_lang(self):
        current_lang = 'en_US'
        other_lang = 'fr_FR'

        lang = self.env['res.lang']._activate_lang(other_lang)
        lang.write({
            'decimal_point': '*',
            'thousands_sep': '/'
        })

        view1 = self.env['ir.ui.view'].create({
            'name': "callee",
            'type': 'qweb',
            'arch': """
                <t t-name="base.callee">
                    <t t-esc="9000000.00" t-options="{'widget': 'float', 'precision': 2}" />
                </t>
            """
        })
        self.env['ir.model.data'].create({
            'name': 'callee',
            'model': 'ir.ui.view',
            'module': 'base',
            'res_id': view1.id,
        })

        view2 = self.env['ir.ui.view'].create({
            'name': "calling",
            'type': 'qweb',
            'arch': """
                <t t-name="base.calling">
                    <t t-call="base.callee" t-lang="'%s'" />
                </t>
            """ % other_lang
        })

        rendered = view2.with_context(lang=current_lang)._render().strip()
        self.assertEqual(rendered, '9/000/000*00')

    def test_render_barcode(self):
        partner = self.env['res.partner'].create({
            'name': 'bacode_test',
            'barcode': 'test'
        })

        view = self.env['ir.ui.view'].create({
            'name': "a_barcode_view",
            'type': 'qweb',
        })

        view.arch = """<div t-field="partner.barcode" t-options="{'widget': 'barcode', 'width': 100, 'height': 30}"/>"""
        rendered = view._render(values={'partner': partner}).strip()
        self.assertRegex(rendered, r'<div><img alt="Barcode test" src="data:image/png;base64,\S+"></div>')

        partner.barcode = '4012345678901'
        view.arch = """<div t-field="partner.barcode" t-options="{'widget': 'barcode', 'symbology': 'EAN13', 'width': 100, 'height': 30, 'img_style': 'width:100%;', 'img_alt': 'Barcode'}"/>"""
        ean_rendered = view._render(values={'partner': partner}).strip()
        self.assertRegex(ean_rendered, r'<div><img style="width:100%;" alt="Barcode" src="data:image/png;base64,\S+"></div>')

        view.arch = """<div t-field="partner.barcode" t-options="{'widget': 'barcode', 'symbology': 'auto', 'width': 100, 'height': 30, 'img_style': 'width:100%;', 'img_alt': 'Barcode'}"/>"""
        auto_rendered = view._render(values={'partner': partner}).strip()
        self.assertRegex(auto_rendered, r'<div><img style="width:100%;" alt="Barcode" src="data:image/png;base64,\S+"></div>')

    def test_render_comment_tail(self):
        """ Test the rendering of a tail text, near a comment.
        """

        view1 = self.env['ir.ui.view'].create({
            'name': "dummy",
            'type': "qweb",
            'arch': """
            <t>
                <!-- it is a comment -->
                <!-- it is another comment -->
                Text 1
                <!-- it is still another comment -->
                Text 2
                <t>ok</t>
            </t>
            """
        })
        emptyline = '\n                '
        expected = markupsafe.Markup('Text 1' + emptyline + emptyline + 'Text 2' + emptyline + 'ok')
        self.assertEqual(view1._render().strip(), expected)

    def test_void_element(self):
        view = self.env['ir.ui.view'].create({
            'name': 'master',
            'type': 'qweb',
            'arch_db': '''<t t-name='master'>
                <meta name="1"/>
                <t t-set="data" t-value="1"/>
                <meta groups="base.group_no_one" name="2"/>
                <meta t-if="False" name="3"/>
                <meta t-if="True" name="4"/>
                <span t-out="1"/>
            </t>'''
        })

        result = '''
                <meta name="1"/>
                <meta name="4"/>
                <span>1</span>
            '''
        rendered = self.env['ir.qweb']._render(view.id)

        self.assertEqual(str(rendered).strip(), result.strip())

    def test_space_remove_technical_space_t_foreach(self):
        view = self.env['ir.ui.view'].create({
            'name': 'master',
            'type': 'qweb',
            'arch_db': '''<t t-name='master'>
                    <section>
                        <article t-foreach="[0, 1, 2]" t-as="value" t-esc="value"/>
                        <t t-foreach="[0, 1, 2]" t-as="value">
                            <article t-esc="value"/>
                        </t>
                    </section>
                </t>'''})

        result = '''
                    <section>
                        <article>0</article><article>1</article><article>2</article>
                            <article>0</article>
                            <article>1</article>
                            <article>2</article>
                    </section>'''

        rendered = self.env['ir.qweb']._render(view.id)

        self.assertEqual(str(rendered), result)

    def test_space_remove_technical_all(self):
        test = self.env['ir.ui.view'].create({
            'name': 'test',
            'type': 'qweb',
            'arch_db': '''<t t-name='test'>
                <span t-out="value"/>
            </t>'''
        })
        self.env.cr.execute("INSERT INTO ir_model_data(name, model, res_id, module)"
                            "VALUES ('test', 'ir.ui.view', %s, 'base')", [test.id])

        view = self.env['ir.ui.view'].create({
            'name': 'master',
            'type': 'qweb',
            'arch_db': '''<t t-name='master'>

                    <section>
                        <meta name="1"/>
                        <t t-set="data" t-value="1"/>
                        <meta groups="base.group_no_one" name="2"/>
                        <meta t-if="False" name="3"/>
                        <meta t-if="True" name="4"/>

                        <article>
                            <t t-foreach="[0, 1]" t-as="value">
                                <t t-call="base.test"/>
                            </t>

                            <t t-if="False">
                                a
                            </t>
                    
                            b

                            <t t-if="True">
                                c <t t-out="1"/>  
                                d
                            </t>
                        </article>


                        <article>
                            <div t-foreach="[0, 1]" t-as="value">
                                <t t-call="base.test"/>
                            </div>

                            <i t-if="False">
                                a
                            </i>
                            <u t-if="False">
                                a
                            </u>
                    
                            b

                            <i t-if="True">
                                c <t t-out="1"/>  
                                d
                            </i>
                        </article>
                    </section>
                </t>'''
        })

        result = '''
                    <section>
                        <meta name="1"/>
                        <meta name="4"/>

                        <article>
                <span>0</span>
                <span>1</span>

                    
                            b

                                c 1  
                                d
                        </article>


                        <article>
                            <div>
                <span>0</span>
                            </div><div>
                <span>1</span>
                            </div>

                    
                            b

                            <i>
                                c 1  
                                d
                            </i>
                        </article>
                    </section>'''

        rendered = self.env['ir.qweb']._render(view.id)
        self.assertEqual(str(rendered), result)

class FileSystemLoader(object):
    def __init__(self, path):
        # TODO: support multiple files #add_file() + add cache
        self.path = path
        self.doc = etree.parse(path).getroot()

    def __iter__(self):
        for node in self.doc:
            name = node.get('t-name')
            if name:
                yield name

    def __call__(self, name, options):
        for node in self.doc:
            if node.get('t-name') == name:
                return (deepcopy(node), name)

class TestQWebStaticXml(TransactionCase):
    matcher = re.compile(r'^qweb-test-(.*)\.xml$')

    def test_render_nodb(self):
        """ Render an html page without db ans wihtout registry
        """
        expected = dedent("""
            <html>
                <head>
                    <title>Odoo</title>
                </head>
                <body>
                    <section class="toto">
                        <div>3</div>
                    </section>
                </body>
            </html>
        """).strip()

        templates = {
            'html': html.document_fromstring("""
                <html t-name="html">
                    <head>
                        <title>Odoo</title>
                    </head>
                    <body>
                        <section class="toto">
                            <t t-call="content"/>
                        </section>
                    </body>
                </html>
            """),
            'content': html.fragment_fromstring("""
                <t t-name="content">
                        <div><t t-out="val"/></div>
                </t>
            """)
        }
        def load(template_name, options):
            return (templates[template_name], template_name)
        rendering = render('html', {'val': 3}, load).strip()

        self.assertEqual(html.document_fromstring(rendering), html.document_fromstring(expected))

    @classmethod
    def get_cases(cls):
        path = cls.qweb_test_file_path()
        return (
            cls("test_qweb_{}".format(cls.matcher.match(f).group(1)))
            for f in os.listdir(path)
            # js inheritance
            if f != 'qweb-test-extend.xml'
            if cls.matcher.match(f)
        )

    @classmethod
    def qweb_test_file_path(cls):
        return os.path.dirname(get_module_resource('web', 'static', 'lib', 'qweb', 'qweb2.js'))

    def __getattr__(self, item):
        if not item.startswith('test_qweb_'):
            raise AttributeError("No {} on {}".format(item, self))

        f = 'qweb-test-{}.xml'.format(item[10:])
        path = self.qweb_test_file_path()

        return lambda: self.run_test_file(os.path.join(path, f))

    @mute_logger('odoo.addons.base.models.ir_qweb') # tests t-raw which is deprecated
    def run_test_file(self, path):
        self.env.user.tz = 'Europe/Brussels'
        doc = etree.parse(path).getroot()
        loader = FileSystemLoader(path)
        for template in loader:
            if not template or template.startswith('_'):
                continue
            param = doc.find('params[@id="{}"]'.format(template))
            # OrderedDict to ensure JSON mappings are iterated in source order
            # so output is predictable & repeatable
            params = {} if param is None else json.loads(param.text, object_pairs_hook=collections.OrderedDict)

            def remove_space(text):
                return re.compile(r'\>[ \n\t]*\<').sub('><', text.strip())

            result = remove_space(doc.find('result[@id="{}"]'.format(template)).text or u'').replace('&quot;', '&#34;')
            rendering = remove_space(self.env['ir.qweb']._render(template, values=params, load=loader))

            self.assertEqual(rendering, result, template)

            rendering_static = None
            try:
                rendering_static = remove_space(render(template, params, loader))
                self.assertEqual(rendering_static, result, "%s (static rendering)" % template)
            except QWebException as e:
                if not isinstance(e.__cause__, NotImplementedError) and "Please use \"env['ir.qweb']._render\" method" in str(e):
                    raise

def load_tests(loader, suite, _):
    # can't override TestQWebStaticXml.__dir__ because dir() called on *class* not
    # instance
    suite.addTests(TestQWebStaticXml.get_cases())
    return suite

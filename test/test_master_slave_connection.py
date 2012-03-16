# Copyright 2009-2010 10gen, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test for master slave connections."""

import datetime
import os
import sys
import time
import unittest
sys.path[0:0] = [""]

from nose.plugins.skip import SkipTest

from bson.son import SON
from bson.tz_util import utc
from pymongo import ReadPreference
from pymongo.errors import ConnectionFailure, InvalidName
from pymongo.errors import CollectionInvalid, OperationFailure
from pymongo.errors import AutoReconnect
from pymongo.database import Database
from pymongo.connection import Connection
from pymongo.collection import Collection
from pymongo.master_slave_connection import MasterSlaveConnection


class TestMasterSlaveConnection(unittest.TestCase):

    def setUp(self):
        host = os.environ.get("DB_IP", "localhost")
        self.master = Connection(host, int(os.environ.get("DB_PORT", 27017)))

        self.slaves = []
        try:
            self.slaves.append(Connection(os.environ.get("DB_IP2", host),
                               int(os.environ.get("DB_PORT2", 27018)),
                               read_preference=ReadPreference.SECONDARY))
        except ConnectionFailure:
            pass

        try:
            self.slaves.append(Connection(os.environ.get("DB_IP3", host),
                               int(os.environ.get("DB_PORT3", 27019)),
                               read_preference=ReadPreference.SECONDARY))
        except ConnectionFailure:
            pass

        if not self.slaves:
            raise SkipTest()

        self.connection = MasterSlaveConnection(self.master, self.slaves)
        self.db = self.connection.pymongo_test

    def test_types(self):
        self.assertRaises(TypeError, MasterSlaveConnection, 1)
        self.assertRaises(TypeError, MasterSlaveConnection, self.master, 1)
        self.assertRaises(TypeError, MasterSlaveConnection, self.master, [1])

    def test_repr(self):
        self.assertEqual(repr(self.connection),
                         "MasterSlaveConnection(%r, %r)" %
                         (self.master, self.slaves))

    def test_disconnect(self):
        class Connection(object):
            def __init__(self):
                self._disconnects = 0

            def disconnect(self):
                self._disconnects += 1

        self.connection._MasterSlaveConnection__master = Connection()
        self.connection._MasterSlaveConnection__slaves = [Connection(),
                                                          Connection()]

        self.connection.disconnect()
        self.assertEquals(1,
            self.connection._MasterSlaveConnection__master._disconnects)
        self.assertEquals(1,
            self.connection._MasterSlaveConnection__slaves[0]._disconnects)
        self.assertEquals(1,
            self.connection._MasterSlaveConnection__slaves[1]._disconnects)

    def test_continue_until_slave_works(self):
        class Slave(object):
            calls = 0

            def __init__(self, fail):
                self._fail = fail

            def _send_message_with_response(self, *args, **kwargs):
                Slave.calls += 1
                if self._fail:
                    raise AutoReconnect()
                return 'sent'

        class NotRandomList(object):
            last_idx = -1

            def __init__(self):
                self._items = [Slave(True), Slave(True),
                               Slave(False), Slave(True)]

            def __len__(self):
                return len(self._items)

            def __getitem__(self, idx):
                NotRandomList.last_idx = idx
                return self._items.pop(0)

        self.connection._MasterSlaveConnection__slaves = NotRandomList()

        response = self.connection._send_message_with_response('message')
        self.assertEquals((NotRandomList.last_idx, 'sent'), response)
        self.assertNotEquals(-1, NotRandomList.last_idx)
        self.assertEquals(3, Slave.calls)

    def test_raise_autoreconnect_if_all_slaves_fail(self):
        class Slave(object):
            calls = 0

            def __init__(self, fail):
                self._fail = fail

            def _send_message_with_response(self, *args, **kwargs):
                Slave.calls += 1
                if self._fail:
                    raise AutoReconnect()
                return 'sent'

        class NotRandomList(object):
            def __init__(self):
                self._items = [Slave(True), Slave(True),
                               Slave(True), Slave(True)]

            def __len__(self):
                return len(self._items)

            def __getitem__(self, idx):
                return self._items.pop(0)

        self.connection._MasterSlaveConnection__slaves = NotRandomList()

        self.assertRaises(AutoReconnect,
            self.connection._send_message_with_response, 'message')
        self.assertEquals(4, Slave.calls)

    def test_get_db(self):

        def make_db(base, name):
            return base[name]

        self.assertRaises(InvalidName, make_db, self.connection, "")
        self.assertRaises(InvalidName, make_db, self.connection, "te$t")
        self.assertRaises(InvalidName, make_db, self.connection, "te.t")
        self.assertRaises(InvalidName, make_db, self.connection, "te\\t")
        self.assertRaises(InvalidName, make_db, self.connection, "te/t")
        self.assertRaises(InvalidName, make_db, self.connection, "te st")

        self.assert_(isinstance(self.connection.test, Database))
        self.assertEqual(self.connection.test, self.connection["test"])
        self.assertEqual(self.connection.test, Database(self.connection,
                                                        "test"))

    def test_database_names(self):
        self.connection.pymongo_test.test.save({"dummy": u"object"})
        self.connection.pymongo_test_mike.test.save({"dummy": u"object"})

        dbs = self.connection.database_names()
        self.assert_("pymongo_test" in dbs)
        self.assert_("pymongo_test_mike" in dbs)

    def test_drop_database(self):
        self.assertRaises(TypeError, self.connection.drop_database, 5)
        self.assertRaises(TypeError, self.connection.drop_database, None)

        raise SkipTest("This test often fails due to SERVER-2329")
        
        self.connection.pymongo_test.test.save({"dummy": u"object"}, safe=True)
        dbs = self.connection.database_names()
        self.assert_("pymongo_test" in dbs)
        self.connection.drop_database("pymongo_test")
        dbs = self.connection.database_names()
        self.assert_("pymongo_test" not in dbs)

        self.connection.pymongo_test.test.save({"dummy": u"object"})
        dbs = self.connection.database_names()
        self.assert_("pymongo_test" in dbs)
        self.connection.drop_database(self.connection.pymongo_test)
        dbs = self.connection.database_names()
        self.assert_("pymongo_test" not in dbs)

    def test_iteration(self):

        def iterate():
            [a for a in self.connection]

        self.assertRaises(TypeError, iterate)

    def test_insert_find_one_in_request(self):
        count = 0
        for i in range(100):
            self.connection.start_request()
            self.db.test.remove({})
            self.db.test.insert({"x": i})
            try:
                if i != self.db.test.find_one()["x"]:
                    count += 1
            except:
                count += 1
            self.connection.end_request()
        self.assertFalse(count)

    # This was failing because commands were being sent to the slaves
    def test_create_collection(self):
        self.connection.drop_database('pymongo_test')

        collection = self.db.create_collection('test')
        self.assert_(isinstance(collection, Collection))

        self.assertRaises(CollectionInvalid, self.db.create_collection, 'test')

    # Believe this was failing for the same reason...
    def test_unique_index(self):
        self.connection.drop_database('pymongo_test')
        self.db.test.create_index('username', unique=True)

        self.db.test.save({'username': 'mike'}, safe=True)
        self.assertRaises(OperationFailure,
                          self.db.test.save, {'username': 'mike'}, safe=True)

    # NOTE this test is non-deterministic, but I expect
    # some failures unless the db is pulling instantaneously...
    def test_insert_find_one_with_slaves(self):
        count = 0
        for i in range(100):
            self.db.test.remove({})
            self.db.test.insert({"x": i})
            try:
                if i != self.db.test.find_one()["x"]:
                    count += 1
            except:
                count += 1
        self.assert_(count)

    # NOTE this test is non-deterministic, but hopefully we pause long enough
    # for the slaves to pull...
    def test_insert_find_one_with_pause(self):
        count = 0

        self.db.test.remove({})
        self.db.test.insert({"x": 5586})
        time.sleep(11)
        for _ in range(10):
            try:
                if 5586 != self.db.test.find_one()["x"]:
                    count += 1
            except:
                count += 1
        self.assertFalse(count)

    def test_kill_cursors(self):

        def cursor_count():
            count = 0
            res = self.connection.master.test_pymongo.command("cursorInfo")
            count += res["clientCursors_size"]
            for slave in self.connection.slaves:
                res = slave.test_pymongo.command("cursorInfo")
                count += res["clientCursors_size"]
            return count

        self.connection.test_pymongo.drop_collection("test")
        db = self.db

        before = cursor_count()

        for i in range(10000):
            db.test.insert({"i": i})
        time.sleep(11)  # need to sleep to be sure this gets pulled...

        self.assertEqual(before, cursor_count())

        for _ in range(10):
            db.test.find_one()

        self.assertEqual(before, cursor_count())

        # Cursors are killed here when the cursor's
        # __del__ method is called before garbage
        # collection. Only CPython's ref counting gc
        # makes this part of the test reliable.
        if not (sys.platform.startswith('java') or
                'PyPy' in sys.version):
            for _ in range(10):
                for x in db.test.find():
                    break

            self.assertEqual(before, cursor_count())

        a = db.test.find()
        for x in a:
            break

        self.assertNotEqual(before, cursor_count())

        if (sys.platform.startswith('java') or
            'PyPy' in sys.version):
            # Explicitly kill cursors.
            a.close()
        else:
            # Implicitly kill them in CPython.
            del a

        self.assertEqual(before, cursor_count())

        a = db.test.find().limit(10)
        for x in a:
            break

        self.assertEqual(before, cursor_count())

    def test_base_object(self):
        c = self.connection
        self.assertFalse(c.slave_okay)
        self.assertTrue(bool(c.read_preference))
        self.assertFalse(c.safe)
        self.assertEqual({}, c.get_lasterror_options())
        db = c.test
        self.assertFalse(db.slave_okay)
        self.assertTrue(bool(c.read_preference))
        self.assertFalse(db.safe)
        self.assertEqual({}, db.get_lasterror_options())
        coll = db.test
        coll.drop()
        self.assertFalse(coll.slave_okay)
        self.assertTrue(bool(c.read_preference))
        self.assertFalse(coll.safe)
        self.assertEqual({}, coll.get_lasterror_options())
        cursor = coll.find()
        self.assertFalse(cursor._Cursor__slave_okay)
        self.assertTrue(bool(cursor._Cursor__read_preference))

        c.safe = True
        w = 1 + len(self.slaves)
        wtimeout=10000 # Wait 10 seconds for replication to complete
        c.set_lasterror_options(w=w, wtimeout=wtimeout)
        self.assertFalse(c.slave_okay)
        self.assertTrue(bool(c.read_preference))
        self.assertTrue(c.safe)
        self.assertEqual({'w': w, 'wtimeout': wtimeout}, c.get_lasterror_options())
        db = c.test
        self.assertFalse(db.slave_okay)
        self.assertTrue(bool(c.read_preference))
        self.assertTrue(db.safe)
        self.assertEqual({'w': w, 'wtimeout': wtimeout}, db.get_lasterror_options())
        coll = db.test
        self.assertFalse(coll.slave_okay)
        self.assertTrue(bool(c.read_preference))
        self.assertTrue(coll.safe)
        self.assertEqual({'w': w, 'wtimeout': wtimeout},
                         coll.get_lasterror_options())
        cursor = coll.find()
        self.assertFalse(cursor._Cursor__slave_okay)
        self.assertTrue(bool(cursor._Cursor__read_preference))

        coll.insert({'foo': 'bar'})
        self.assertEquals(1, coll.find({'foo': 'bar'}).count())
        self.assert_(coll.find({'foo': 'bar'}))
        coll.remove({'foo': 'bar'})
        self.assertEquals(0, coll.find({'foo': 'bar'}).count())

        # Set self.connection back to defaults
        c.safe = False
        c.unset_lasterror_options()
        self.assertFalse(self.connection.slave_okay)
        self.assertTrue(bool(self.connection.read_preference))
        self.assertFalse(self.connection.safe)
        self.assertEqual({}, self.connection.get_lasterror_options())

    def test_document_class(self):
        c = MasterSlaveConnection(self.master, self.slaves)
        db = c.pymongo_test
        db.test.insert({"x": 1})
        time.sleep(1)

        self.assertEqual(dict, c.document_class)
        self.assert_(isinstance(db.test.find_one(), dict))
        self.assertFalse(isinstance(db.test.find_one(), SON))

        c.document_class = SON

        self.assertEqual(SON, c.document_class)
        self.assert_(isinstance(db.test.find_one(), SON))
        self.assertFalse(isinstance(db.test.find_one(as_class=dict), SON))

        c = MasterSlaveConnection(self.master, self.slaves, document_class=SON)
        db = c.pymongo_test

        self.assertEqual(SON, c.document_class)
        self.assert_(isinstance(db.test.find_one(), SON))
        self.assertFalse(isinstance(db.test.find_one(as_class=dict), SON))

        c.document_class = dict

        self.assertEqual(dict, c.document_class)
        self.assert_(isinstance(db.test.find_one(), dict))
        self.assertFalse(isinstance(db.test.find_one(), SON))

    def test_tz_aware(self):
        dt = datetime.datetime.utcnow()
        conn = MasterSlaveConnection(self.master, self.slaves)
        self.assertEquals(False, conn.tz_aware)
        db = conn.pymongo_test
        db.tztest.insert({'dt': dt}, safe=True)
        time.sleep(0.5)
        self.assertEqual(None, db.tztest.find_one()['dt'].tzinfo)

        conn = MasterSlaveConnection(self.master, self.slaves, tz_aware=True)
        self.assertEquals(True, conn.tz_aware)
        db = conn.pymongo_test
        db.tztest.insert({'dt': dt}, safe=True)
        time.sleep(0.5)
        self.assertEqual(utc, db.tztest.find_one()['dt'].tzinfo)

        conn = MasterSlaveConnection(self.master, self.slaves, tz_aware=False)
        self.assertEquals(False, conn.tz_aware)
        db = conn.pymongo_test
        db.tztest.insert({'dt': dt})
        time.sleep(0.5)
        self.assertEqual(None, db.tztest.find_one()['dt'].tzinfo)


if __name__ == "__main__":
    unittest.main()

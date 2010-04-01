###
# Copyright (c) 2002-2005, Jeremiah Fincher
# Copyright (c) 2009-2010, James Vega
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

import os
import time
import string

import supybot.conf as conf
import supybot.ircdb as ircdb
import supybot.utils as utils
from supybot.commands import *
import supybot.plugins as plugins
import supybot.ircutils as ircutils
import supybot.callbacks as callbacks

#try:
    #import sqlite3 as sqlite
#except ImportError:
    #raise callbacks.Error, 'You need to have PySQLite installed to use this ' \
                           #'plugin.  Download it at ' \
                           #'<http://code.google.com/p/pysqlite/>'

try:
    import sqlite3
except ImportError:
    from pysqlite2 import dbapi2 as sqlite3 # for python2.4

# these are needed cuz we are overriding getdb
import threading
import supybot.world as world

def getFactoid(irc, msg, args, state):
    assert not state.channel
    callConverter('channel', irc, msg, args, state)
    separator = state.cb.registryValue('learnSeparator', state.channel)
    try:
        i = args.index(separator)
    except ValueError:
        raise callbacks.ArgumentError
    args.pop(i)
    key = []
    value = []
    for (j, s) in enumerate(args[:]):
        if j < i:
            key.append(args.pop(0))
        else:
            value.append(args.pop(0))
    if not key or not value:
        raise callbacks.ArgumentError
    state.args.append(' '.join(key))
    state.args.append(' '.join(value))

def getFactoidId(irc, msg, args, state):
    Type = 'key id'
    p = lambda i: i > 0
    callConverter('int', irc, msg, args, state, Type, p)

addConverter('factoid', getFactoid)
addConverter('factoidId', getFactoidId)

class Factoids(callbacks.Plugin, plugins.ChannelDBHandler):
    def __init__(self, irc):
        callbacks.Plugin.__init__(self, irc)
        plugins.ChannelDBHandler.__init__(self)

    def makeDb(self, filename):
        if os.path.exists(filename):
            db = sqlite3.connect(filename)
            db.text_factory = str
            return db
        db = sqlite3.connect(filename)
        db.text_factory = str
        cursor = db.cursor()
        cursor.execute("""CREATE TABLE keys (
                          id INTEGER PRIMARY KEY,
                          key TEXT UNIQUE ON CONFLICT IGNORE,
                          locked BOOLEAN
                          )""")
        cursor.execute("""CREATE TABLE factoids (
                          id INTEGER PRIMARY KEY,
                          key_id INTEGER,
                          added_by TEXT,
                          added_at TIMESTAMP,
                          usage_count INTEGER,
                          fact TEXT
                          )""")
        cursor.execute("""CREATE TRIGGER remove_factoids
                          BEFORE DELETE ON keys
                          BEGIN
                            DELETE FROM factoids WHERE key_id = old.id;
                          END
                       """)
        db.commit()
        return db

    # override this because sqlite3 doesn't have autocommit
    # use isolation_level instead.
    def getDb(self, channel):
        """Use this to get a database for a specific channel."""
        currentThread = threading.currentThread()
        if channel not in self.dbCache and currentThread == world.mainThread:
            self.dbCache[channel] = self.makeDb(self.makeFilename(channel))
        if currentThread != world.mainThread:
            db = self.makeDb(self.makeFilename(channel))
        else:
            db = self.dbCache[channel]
        db.isolation_level = None
        return db

    def getCommandHelp(self, command, simpleSyntax=None):
        method = self.getCommandMethod(command)
        if method.im_func.func_name == 'learn':
            chan = None
            if dynamic.msg is not None:
                chan = dynamic.msg.args[0]
            s = self.registryValue('learnSeparator', chan)
            help = callbacks.getHelp
            if simpleSyntax is None:
                simpleSyntax = conf.get(conf.supybot.reply.showSimpleSyntax,
                                        chan)
            if simpleSyntax:
                help = callbacks.getSyntax
            return help(method,
                        doc=method._fake__doc__ % (s, s),
                        name=callbacks.formatCommand(command))
        return super(Factoids, self).getCommandHelp(command, simpleSyntax)

    def learn(self, irc, msg, args, channel, key, factoid):
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("SELECT id, locked FROM keys WHERE key LIKE ?", (key,))
        results = cursor.fetchall()
        if len(results) == 0:
            cursor.execute("""INSERT INTO keys VALUES (NULL, ?, 0)""", (key,))
            db.commit()
            cursor.execute("SELECT id, locked FROM keys WHERE key LIKE ?", (key,))
            results = cursor.fetchall()
        (id, locked) = map(int, results[0])
        capability = ircdb.makeChannelCapability(channel, 'factoids')
        if not locked:
            if ircdb.users.hasUser(msg.prefix):
                name = ircdb.users.getUser(msg.prefix).name
            else:
                name = msg.nick
            cursor.execute("""INSERT INTO factoids VALUES
                              (NULL, ?, ?, ?, ?, ?)""",
                           (id, name, int(time.time()), 0, factoid))
            db.commit()
            irc.replySuccess()
        else:
            irc.error('That factoid is locked.')
    learn = wrap(learn, ['factoid'])
    learn._fake__doc__ = """[<channel>] <key> %s <value>

                         Associates <key> with <value>.  <channel> is only
                         necessary if the message isn't sent on the channel
                         itself.  The word '%s' is necessary to separate the
                         key from the value.  It can be changed to another word
                         via the learnSeparator registry value.
                         """


    def _lookupFactoid(self, channel, key):
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT factoids.fact, factoids.id FROM factoids, keys
                          WHERE keys.key LIKE ? AND factoids.key_id=keys.id
                          ORDER BY factoids.id
                          LIMIT 20""", (key,))
        return cursor.fetchall()
        #return [t[0] for t in cursor.fetchall()]
    
    def _searchFactoid(self, channel, key):
        db = self.getDb(channel)
        cursor = db.cursor()
        key = '%' + key + '%'
        cursor.execute("""SELECT key FROM keys
                          WHERE key LIKE ?
                          LIMIT 20""", (key,))
        return cursor.fetchall()
    
    
    def _updateRank(self, channel, factoids):
        if self.registryValue('keepRankInfo', channel):
            db = self.getDb(channel)
            cursor = db.cursor()
            for (fact,id) in factoids:
                cursor.execute("""SELECT factoids.usage_count
                          FROM factoids
                          WHERE factoids.id=?""", (id,))
                old_count = cursor.fetchall()[0][0]
                cursor.execute("UPDATE factoids SET usage_count=? WHERE id=?", (old_count + 1, id,))
                db.commit()
        
    def _replyFactoids(self, irc, msg, key, channel, factoids,
                       number=0, error=True):
        if factoids:
            if number:
                try:
                    irc.reply(factoids[number-1][0])
                    self._updateRank(channel, [factoids[number-1]])
                except IndexError:
                    irc.error('That\'s not a valid number for that key.')
                    return
            else:
                env = {'key': key}
                def prefixer(v):
                    env['value'] = v
                    formatter = self.registryValue('format', msg.args[0])
                    return ircutils.standardSubstitute(irc, msg,
                                                       formatter, env)
                if len(factoids) == 1:
                    irc.reply(prefixer(factoids[0][0]))
                else:
                    factoidsS = []
                    counter = 1
                    for factoid in factoids:
                        factoidsS.append(format('(#%i) %s', counter, factoid[0]))
                        counter += 1
                    irc.replies(factoidsS, prefixer=prefixer,
                                joiner=', or ', onlyPrefixFirst=True)
                self._updateRank(channel, factoids)
        elif error:
            irc.error('No factoid matches that key.')

    def invalidCommand(self, irc, msg, tokens):
        if irc.isChannel(msg.args[0]):
            channel = msg.args[0]
            if self.registryValue('replyWhenInvalidCommand', channel):
                key = ' '.join(tokens)
                factoids = self._lookupFactoid(channel, key)
                if factoids:
                    self._replyFactoids(irc, msg, key, channel, factoids, error=False)
                else:
                    if self.registryValue('replyWhenInvalidCommandSearchKeys'):
                        factoids = self._searchFactoid(channel, key)
                        #print 'searchfactoids result:', factoids, '>'
                        if factoids:
                            keylist = ["'%s'" % (fact[0],) for fact in factoids]
                            keylist = ', '.join(keylist)
                            irc.reply("I do not know about '%s', but I do know about these similar topics: %s" % (key, keylist))

    def whatis(self, irc, msg, args, channel, words):
        """[<channel>] <key> [<number>]

        Looks up the value of <key> in the factoid database.  If given a
        number, will return only that exact factoid.  <channel> is only
        necessary if the message isn't sent in the channel itself.
        """
        number = None
        if len(words) > 1:
            if words[-1].isdigit():
                number = int(words.pop())
                if number <= 0:
                    irc.errorInvalid('key id')
        key = ' '.join(words)
        factoids = self._lookupFactoid(channel, key)
        self._replyFactoids(irc, msg, key, channel, factoids, number)
    whatis = wrap(whatis, ['channel', many('something')])

    def rank(self, irc, msg, args, channel):
        """[<channel>]
        
        Returns a list of top-ranked factoid keys, sorted by usage count 
        (rank). The number of factoid keys returned is set by the 
        rankListLength registry value. <channel> is only necessary if the 
        message isn't sent in the channel itself.
        """
        numfacts = self.registryValue('rankListLength', channel)
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT keys.key, factoids.usage_count
                          FROM keys, factoids
                          WHERE factoids.key_id=keys.id
                          ORDER BY factoids.usage_count DESC
                          LIMIT ?""", (numfacts,))
        factkeys = cursor.fetchall()
        s = [ "#%d %s (%d)" % (i+1, key[0], key[1]) for i, key in enumerate(factkeys) ]
        irc.reply(", ".join(s))
    rank = wrap(rank, ['channel'])
    
    def lock(self, irc, msg, args, channel, key):
        """[<channel>] <key>

        Locks the factoid(s) associated with <key> so that they cannot be
        removed or added to.  <channel> is only necessary if the message isn't
        sent in the channel itself.
        """
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("UPDATE keys SET locked=1 WHERE key LIKE ?", (key,))
        db.commit()
        irc.replySuccess()
    lock = wrap(lock, ['channel', 'text'])

    def unlock(self, irc, msg, args, channel, key):
        """[<channel>] <key>

        Unlocks the factoid(s) associated with <key> so that they can be
        removed or added to.  <channel> is only necessary if the message isn't
        sent in the channel itself.
        """
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("UPDATE keys SET locked=0 WHERE key LIKE ?", (key,))
        db.commit()
        irc.replySuccess()
    unlock = wrap(unlock, ['channel', 'text'])

    def forget(self, irc, msg, args, channel, words):
        """[<channel>] <key> [<number>|*]

        Removes the factoid <key> from the factoids database.  If there are
        more than one factoid with such a key, a number is necessary to
        determine which one should be removed.  A * can be used to remove all
        factoids associated with a key.  <channel> is only necessary if
        the message isn't sent in the channel itself.
        """
        number = None
        if len(words) > 1:
            if words[-1].isdigit():
                number = int(words.pop())
                if number <= 0:
                    irc.errorInvalid('key id')
            elif words[-1] == '*':
                words.pop()
                number = True
        key = ' '.join(words)
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT keys.id, factoids.id
                          FROM keys, factoids
                          WHERE key LIKE ? AND
                                factoids.key_id=keys.id""", (key,))
        results = cursor.fetchall()
        if len(results) == 0:
            irc.error('There is no such factoid.')
        elif len(results) == 1 or number is True:
            (id, _) = results[0]
            cursor.execute("""DELETE FROM factoids WHERE key_id=?""", (id,))
            cursor.execute("""DELETE FROM keys WHERE key LIKE ?""", (key,))
            db.commit()
            irc.replySuccess()
        else:
            if number is not None:
                #results = cursor.fetchall()
                try:
                    (_, id) = results[number-1]
                except IndexError:
                    irc.error('Invalid factoid number.')
                    return
                cursor.execute("DELETE FROM factoids WHERE id=?", (id,))
                db.commit()
                irc.replySuccess()
            else:
                irc.error('%s factoids have that key.  '
                          'Please specify which one to remove, '
                          'or use * to designate all of them.' %
                          len(results))
    forget = wrap(forget, ['channel', many('something')])

    def random(self, irc, msg, args, channel):
        """[<channel>]

        Returns a random factoid from the database for <channel>.  <channel>
        is only necessary if the message isn't sent in the channel itself.
        """
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT fact, key_id FROM factoids
                          ORDER BY random()
                          LIMIT 3""")
        results = cursor.fetchall()
        if len(results) != 0:
            L = []
            for (factoid, id) in results:
                cursor.execute("""SELECT key FROM keys WHERE id=?""", (id,))
                (key,) = cursor.fetchone()
                L.append('"%s": %s' % (ircutils.bold(key), factoid))
            irc.reply('; '.join(L))
        else:
            irc.error('I couldn\'t find a factoid.')
    random = wrap(random, ['channel'])

    def info(self, irc, msg, args, channel, key):
        """[<channel>] <key>

        Gives information about the factoid(s) associated with <key>.
        <channel> is only necessary if the message isn't sent in the channel
        itself.
        """
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("SELECT id, locked FROM keys WHERE key LIKE ?", (key,))
        results = cursor.fetchall()
        if len(results) == 0:
            irc.error('No factoid matches that key.')
            return
        (id, locked) = map(int, results[0])
        cursor.execute("""SELECT  added_by, added_at, usage_count FROM factoids
                          WHERE key_id=?
                          ORDER BY id""", (id,))
        factoids = cursor.fetchall()
        L = []
        counter = 0
        for (added_by, added_at, usage_count) in factoids:
            counter += 1
            added_at = time.strftime(conf.supybot.reply.format.time(),
                                     time.localtime(int(added_at)))
            L.append(format('#%i was added by %s at %s, and has been recalled '
                            '%n',
                            counter, added_by, added_at, (usage_count, 'time')))
        factoids = '; '.join(L)
        s = format('Key %q is %s and has %n associated with it: %s',
                   key, locked and 'locked' or 'not locked',
                   (counter, 'factoid'), factoids)
        irc.reply(s)
    info = wrap(info, ['channel', 'text'])

    def change(self, irc, msg, args, channel, key, number, replacer):
        """[<channel>] <key> <number> <regexp>

        Changes the factoid #<number> associated with <key> according to
        <regexp>.
        """
        db = self.getDb(channel)
        cursor = db.cursor()
        cursor.execute("""SELECT factoids.id, factoids.fact
                          FROM keys, factoids
                          WHERE keys.key LIKE ? AND
                                keys.id=factoids.key_id""", (key,))
        results = cursor.fetchall()
        if len(results) == 0:
            irc.error(format('I couldn\'t find any key %q', key))
            return
        elif len(results) < number:
            irc.errorInvalid('key id')
        (id, fact) = results[number-1]
        newfact = replacer(fact)
        cursor.execute("UPDATE factoids SET fact=? WHERE id=?", (newfact, id))
        db.commit()
        irc.replySuccess()
    change = wrap(change, ['channel', 'something',
                           'factoidId', 'regexpReplacer'])

    _sqlTrans = string.maketrans('*?', '%_')
    def search(self, irc, msg, args, channel, optlist, globs):
        """[<channel>] [--values] [--{regexp} <value>] [<glob> ...]

        Searches the keyspace for keys matching <glob>.  If --regexp is given,
        it associated value is taken as a regexp and matched against the keys.
        If --values is given, search the value space instead of the keyspace.
        """
        if not optlist and not globs:
            raise callbacks.ArgumentError
        tables = ['keys']
        formats = []
        criteria = []
        target = 'keys.key'
        predicateName = 'p'
        db = self.getDb(channel)
        for (option, arg) in optlist:
            if option == 'values':
                target = 'factoids.fact'
                if 'factoids' not in tables:
                    tables.append('factoids')
                criteria.append('factoids.key_id=keys.id')
            elif option == 'regexp':
                criteria.append('%s(TARGET)' % predicateName)
                def p(s, r=arg):
                    return int(bool(r.search(s)))
                db.create_function(predicateName, 1, p)
                predicateName += 'p'
        for glob in globs:
            criteria.append('TARGET LIKE ?')
            formats.append(glob.translate(self._sqlTrans))
        cursor = db.cursor()
        sql = """SELECT keys.key FROM %s WHERE %s""" % \
              (', '.join(tables), ' AND '.join(criteria))
        sql = sql + " ORDER BY keys.key"
        sql = sql.replace('TARGET', target)
        cursor.execute(sql, formats)
        results = cursor.fetchall()
        if len(results) == 0:
            irc.reply('No keys matched that query.')
        elif len(results) == 1 and \
             self.registryValue('showFactoidIfOnlyOneMatch', channel):
            self.whatis(irc, msg, [results[0][0]])
        elif len(results) > 100:
            irc.reply('More than 100 keys matched that query; '
                      'please narrow your query.')
        else:
            keys = [repr(t[0]) for t in results]
            s = format('%L', keys)
            irc.reply(s)
    search = wrap(search, ['channel',
                           getopts({'values': '', 'regexp': 'regexpMatcher'}),
                           any('glob')])


Class = Factoids


# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:

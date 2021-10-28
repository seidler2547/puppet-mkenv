#!/usr/bin/env python3

from pyparsing import (
  QuotedString,
  Group,
  Keyword,
  ZeroOrMore,
  Suppress,
  LineEnd,
  Literal,
  ParserElement,
  FollowedBy,
  restOfLine
)
import requests
import pprint
import sys
import semver
import functools
import re
from multiprocessing import Pool
from requests_cache import CachedSession

session = CachedSession()

flatten = lambda l: [item for sublist in l for item in sublist]

def add_module(slug, parent='Puppetfile', versions = []):
  nslug = slug.replace('/', '-')
  splitslug = nslug.split('-')
  modname = splitslug[-1]
  if modname in modules:
    if modules[modname]['slug'] != nslug:
      raise Exception('Conflicting modules {} (from {}) and {} (from {})'
        .format(modules[modname]['slug'], modules[modname]['_mkenv_versions'].keys(), nslug, parent))
    if not parent in modules[modname]['_mkenv_versions']:
      modules[modname]['_mkenv_versions'][parent] = versions
      modules[modname].pop('uri', None)
    return
  modules[modname] = { 'slug': nslug, '_mkenv_versions': { parent: versions } }

def pp_deps(modname):
  ret = []
  for m in sorted(modules[modname]['_mkenv_versions'].items(), key=lambda x: x[1][-1].strip('<>=') if len(x[1])>0 else '0'):
    ret.append('{} requires {}'.format(m[0], str(m[1]).strip('[]')))
  return "; ".join(ret)

def qsHelper(a,low,high): 
    i = low-1
    pivot = a[high]
    for j in range(low , high):
        if semver.compare(a[j]['version'], pivot['version']) >= 0:
            i = i+1
            a[i], a[j] = a[j], a[i] 
    a[i+1], a[high] = a[high], a[i+1]
    return i+1

def vSort(a,low=0,high=-100):
  if high == -100:
    high = len(a)-1
  if low < high:
    pi = qsHelper(a, low, high)
    vSort(a, low, pi-1)
    vSort(a, pi+1, high)

def fix_semver(wrong):
  match = re.match(r'([0-9]+)\.x', wrong)
  if match is not None:
    return ['>={}.0.0'.format(match.group(1)),
            '<{}.0.0'.format(str(int(match.group(1))+1))]
  match = re.match(r'([0-9]+\.){2}[0-9]+', wrong)
  if match is not None:
    return ['=={}'.format(match.group(0))]
  return [wrong]

def debug_match(a,b):
  pprint.pprint(a)
  pprint.pprint(b)
  return semver.match(a,b)

def get_module_info(slug):
  print('Checking '+ slug)
  sys.stdout.flush()
  resp = session.get(forgeUrl + '/v3/modules/' + slug + '?exclude_fields=readme%20changelog%20license%20reference')
  modinfo = resp.json()
  name = modinfo['name']
  thismod = modules[name]
  if 'releases' in modinfo:
    thismod.pop('_mkenv_matchversion', None)
    vSort(modinfo['releases'])
    for r in modinfo['releases']:
      print('  version {}'.format(r['version']))
      if functools.reduce(
        lambda x,y: x and semver.match(r['version'], y),
        functools.reduce(
          lambda x,y: x + fix_semver(y),
          flatten(thismod['_mkenv_versions'].values()),
          []
        ),
        True):
        print('    matches requirements: {}'.format(pp_deps(name)))
        thismod['_mkenv_matchversion'] = r['version']
        break
  else:
    print('thismodersions?')
  #pprint.pprint(modinfo)
  return { name: {**modinfo, **thismod } }

def fetch_modules_info():
  #with Pool(4) as p:
    modnames = map(lambda v: v['slug'], filter(lambda d: 'uri' not in d, modules.values()))
    [modules.update(u) for u in map(get_module_info, modnames)]

def add_dependencies():
  for m in list(filter(lambda x: 'current_release' in x, modules.values())):
    for d in m['current_release']['metadata']['dependencies']:
      add_module(
        d['name'], 
        m['slug'], 
        re.sub(
          r'([<>=]+[0-9.]+)', 
          r'\1 ', 
          str(d['version_requirement']).replace(' ', '')).split())

# some helpful parse matchers
AnyQuoted = (QuotedString('"') ^ QuotedString("'"))
ModLine = Keyword('mod') + AnyQuoted + ZeroOrMore(Suppress(Literal(',')) + AnyQuoted)

# basic Puppetfile syntax
grammar = (
  Group(Keyword('forge') + AnyQuoted) +
  ZeroOrMore(Group(ModLine)) +
  FollowedBy(LineEnd())
).ignore(Literal('#') + restOfLine + Suppress(LineEnd()))

ParserElement.setDefaultWhitespaceChars(" \t\n")
# parse the Puppetfile
parsed = grammar.parseFile('Puppetfile')

modules = {}

# interpret the parsed file contents
for line in parsed:
  if line[0] == 'forge':
    forgeUrl = line[1]
    print('Forge URL: {}'.format(forgeUrl))
  if line[0] == 'mod':
    if len(line) > 2 and not line[2].startswith(':'):
      versions = line[2].split()
    else:
      versions = []
    add_module(line[1], "Puppetfile", versions)
    print('Declared Module: {} {}'.format(line[1], versions))

# resolve dependencies
while len(list(filter(lambda d: 'uri' not in d, modules.values()))) > 0:
  print(len(modules))
  print(len(list(filter(lambda d: 'uri' not in d, modules.values()))))
  fetch_modules_info()
  add_dependencies()

for m in modules.values():
  if not '_mkenv_matchversion' in m:
    print('Could not resolve a version for {}, requirements are:\n  {}'.format(m['slug'], pp_deps(m['name']).replace(';', '\n ')))
  else:
    print('Module {} resolved to {} version {}'.format(m['name'], m['slug'], m['_mkenv_matchversion']))
#pprint.pprint (modules)

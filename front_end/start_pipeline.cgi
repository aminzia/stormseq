#!/usr/bin/env python
import cgi
import os, glob, sys, time, re
import json
import commands, subprocess
from collections import defaultdict
from multiprocessing import Process
import cgitb; cgitb.enable()  # for troubleshooting
from helpers import *
import string

redirect_url = "/"

request_quotas = {
  'normal': 18,
  'spot': 98
}

form = cgi.FieldStorage()
parameters = json.loads(form.getvalue('all_objects'))

f = open("/tmp/start_log.txt","w")
f.write('Input is:\n%s\n' % '\n'.join(['%s:\t%s' % (x, parameters[x]) for x in parameters]))
f.flush()

setup_s3cfg(parameters)
if parameters['number_of_genomes'] == 'multiple_individuals':
  samples = set(parameters['sample_names'])
  if '' in samples: samples.remove('')
else:
  samples = [parameters['sample_name']]

write_basic_config_file(parameters, '')
get_zones_command = "sudo starcluster lz"
stdout = subprocess.check_output(get_zones_command.split())
zones = [line.strip().split()[-1] for line in stdout.split('\n') if line.startswith('name')]
samples_per_zone = [max(1, len(samples)/len(zones) + int(len(samples) % len(zones) > i)) for i in range(len(zones))]

f.write('Samples: ' + ' '.join(samples) + '\n')

def start_sample(index, sample, dir=None):
  f.write('Sample %s: %s (directory: %s)\n' % (index, sample, dir))
  files, ext, all_file_size = check_files(parameters, dir, f)
  if files is None:
    file_error('%s not found' % sample)
  f.write('\n'.join(files) + '\n')
  f.flush()
  
  # Setup starcluster
  sample = re.sub('\s', '_', sample)
  if index == 0:
    write_basic_config_file(parameters, sample)
  
  zone = zones[index % len(zones)]
  parameters['zone'] = zone
  parameters['samples_on_zone'] = samples_per_zone[index % len(zones)]
  
  parameters['max_on_zone'] = request_quotas[parameters['request_type']]/parameters['samples_on_zone']
  parameters['number_of_processes'] = min(len(files), parameters['max_on_zone'])
  add_to_config_file(parameters, sample)
  
  try:
    command = 'sudo starcluster terminate -c stormseq_%s' % sample
    stdout = subprocess.check_output(command.split())
  except Exception, e:
    pass
  
  try:
    check_volume_command = "sudo starcluster lv --name=stormseq_%s" % sample
    stdout = subprocess.check_output(check_volume_command.split())
    volume_id = [line.split()[1] for line in stdout.split('\n') if line.find('volume_id') > -1][0]
    check_volume_command = "sudo starcluster rv -c %s" % volume_id
    stdout = subprocess.check_output(check_volume_command.split())
  except Exception, e:
    pass
  
  try:
    command = 'sudo starcluster rk -c stormseq_starcluster_%s' % (sample)
    stdout = subprocess.check_output(command.split())
  except Exception, e:
    pass
  
  try:
    command = 'rm /root/.ssh/stormseq_starcluster_%s.rsa' % (sample)
    stdout = subprocess.check_output(command.split())
  except Exception, e:
    pass

  command = 'sudo starcluster createkey -o /root/.ssh/stormseq_starcluster_%s.rsa stormseq_starcluster_%s' % (sample, sample)
  stdout = subprocess.check_output(command.split())
  f.write(stdout + '\n')
  
  # Create volume for NFS share
  size = min(1000, all_file_size)
  while True:
    f.write('Trying zone %s\n' % zone)
    f.flush()
    try:
      while True:
        try:
          create_volume_command = "sudo starcluster createvolume --shutdown-volume-host --name=stormseq_%s %s %s" % (sample, size, zone)
          stdout = subprocess.check_output(create_volume_command.split(), stderr=subprocess.STDOUT)
          break
        except subprocess.CalledProcessError, e:
          f.write('Volume error: ' + str(e.output) + '\n')
          if e.output.find('The requested Availability Zone is currently constrained') == -1:
            generic_response('Failed to create volume. Error: %s' % e.output)
          raise
      
      f.write('Successfully created volume in %s\n' % zone)
      check_volume_command = "sudo starcluster lv --name=stormseq_%s" % sample
      stdout = subprocess.check_output(check_volume_command.split())
      if stdout.find('volume_id') == -1:
        generic_response('Failed to create volume')
      volume_id = [line.split()[1] for line in stdout.split('\n') if line.find('volume_id') > -1][0]
      add_volume_to_config_file(volume_id, sample)
      while stdout.find('available') == -1:
        time.sleep(5)
        stdout = subprocess.check_output(check_volume_command.split())
      f.write('Successfully found volume in %s\n' % zone)
      
      # Start cluster
      start_command = 'sudo timelimit -T 1 -t 1200 starcluster start --force-spot-master --cluster-template=stormseq_%s stormseq_%s' % (sample, sample)
      f.write(start_command + '\n')
      started = False
      for i in range(3):
        try:
          stdout = subprocess.check_output(start_command.split(), stderr=subprocess.STDOUT)
          f.write(stdout + '\n')
          f.flush()
          if stdout.find('The cluster is now ready to use.') == -1:
            kill_cmd = 'starcluster terminate -c stormseq_%s' % (sample)
            subprocess.check_output(kill_cmd.split())
            continue
          started = True
          break
        except subprocess.CalledProcessError, e:
          f.write('Starting error (%s): %s\n' % (i, e.output))
          raise
      if not started:
        cluster_fail('Something went wrong starting the cluster')
      break
    except subprocess.CalledProcessError, e:
      f.write('Tried zone %s and failed\n' % zone)
      f.write('Error: ' + str(e.output) + '\n')
      if e.output.find('The requested Availability Zone is currently constrained') > -1:
        zones.pop(index % len(zones))
        samples_per_zone.pop(index % len(zones))
        zone = zones[index % len(zones)]
        replace_zone_in_config_file(zone, sample)
        remove_volume_from_config_file(sample)
    
  config_file = 'stormseq_%s.cnf' % sample
  with open(config_file, 'w') as cnf:
    cnf.write(json.dumps({ 'files' : files, 'parameters' : parameters, 'sample' : sample }))
  put_conf_file_command = 'sudo starcluster put stormseq_%s %s /mydata/' % (sample, config_file)
  tries = 0
  while True:
    stdout = commands.getoutput(put_conf_file_command)
    f.write(stdout + '\n')
    f.flush()
    if stdout.find('ERROR') > -1:
      tries += 1
      if tries == 3:
        cluster_fail('Something went wrong starting the cluster')
    else:
      break
  
  # Run commands
  command = ('sudo starcluster sshmaster stormseq_%s' % sample).split()
  command.append("'qsub -cwd -b y python run_mapping.py --config_file=/mydata/%s'" % config_file)
  f.write(' '.join(command) + '\n')
  stdout = subprocess.check_output(command)
  
  f.write(json.dumps(parameters) + '\n')
  p = subprocess.Popen(['python', '/var/www/run_cleaning.py', config_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
  f.write('%s\n' % p.pid)
  
  p = subprocess.Popen(['python', '/var/www/check_cluster.py', config_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
  f.write('%s\n' % p.pid)

all_start_pipelines = []
if parameters['number_of_genomes'] == 'multiple_individuals':
  for index, sample in enumerate(samples):
    start_sample(index, sample, sample)
  if parameters['joint_calling']:
    p = subprocess.Popen(['python', '/var/www/joint_calling.py', json.dumps(parameters)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
    f.write('%s\n' % p.pid)
else:
  start_sample(0, sample)

f.close()
print 'Content-Type: text/html'
print
sys.stdout.write('success')
sys.exit()
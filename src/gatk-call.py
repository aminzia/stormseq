import sys
import os
import subprocess
from optparse import OptionParser
from multiprocessing import Process
import commands
import re
import boto

root = '/usr/local/bin'

parser = OptionParser()
parser.add_option('--bam', help='Input (merged) BAM file')
parser.add_option('--dbsnp', help='dbSNP VCF file')
parser.add_option('--reference', help='Genome FASTA file')
parser.add_option('--chromosome', help='Chromosome')
parser.add_option('--stand_call_conf', help='Standard min confidence threshold for calling', default='30.0')
parser.add_option('--stand_emit_conf', help='Standard min confidence threshold for emitting', default='30.0')
parser.add_option('--output_gvcf', action="store_true", help='Call ALL sites in dbSNP as well as novel variants', default=False)
parser.add_option('--intervals', help='Intervals file (for exome seq, e.g.)', default=None)
parser.add_option('--indels', action='store_true', help='Call indels in addition to of SNPs', default=False)

(options, args) = parser.parse_args()

gatk_binary = '%s/GenomeAnalysisTKLite-2.1-12-g2d7797a/GenomeAnalysisTKLite.jar' % root
samtools_binary = '%s/samtools' % root

dbsnp = options.dbsnp
ref = options.reference
chromosome = options.chromosome
dbsnp_chr = dbsnp.replace('.vcf', '_%s.vcf' % chromosome)
gatk_options = '-stand_call_conf %s -stand_emit_conf %s' % (options.stand_call_conf, options.stand_emit_conf)

recal_bam = '-I ' + re.sub('.merged.bam$', '_%s.recal.bam' % chromosome, options.bam)
vcf = re.sub('.merged.bam$', '_%s.vcf' % chromosome, options.bam)

try:
  if options.output_gvcf:
    command = 'java -Xmx6500m -jar %s -T UnifiedGenotyper --dbsnp %s --output_mode EMIT_ALL_SITES -L %s -R %s %s %s' % (gatk_binary, dbsnp, chromosome, ref, recal_bam, gatk_options)
    command += ' --genotype_likelihoods_model BOTH' if options.indels else ''
    command += '' if options.intervals is None else ' -L %s' % options.intervals
    command += '' if options.intervals is None or command.find('--interval_set_rule INTERSECTION') > -1 else ' --interval_set_rule INTERSECTION'
    command += ' | gatk_to_gvcf > %s' % (vcf)
    print command
    exit_status, stdout = commands.getstatusoutput(command)
    print exit_status, stdout
  else:
    command = 'java -Xmx6500m -jar %s -T UnifiedGenotyper -L %s -R %s %s -o %s --dbsnp %s %s' % (gatk_binary, chromosome, ref, recal_bam, vcf, dbsnp, gatk_options)
    command += ' --genotype_likelihoods_model BOTH' if options.indels else ''
    command += '' if options.intervals is None else ' -L %s' % options.intervals
    command += '' if options.intervals is None or command.find('--interval_set_rule INTERSECTION') > -1 else ' --interval_set_rule INTERSECTION'
    print command
    exit_status, stdout = commands.getstatusoutput(command)
    print exit_status, stdout
  
  exit_status, stdout = commands.getstatusoutput('touch %s.done' % (vcf))
  print exit_status, stdout
except Exception, e:
  print >> sys.stderr, e
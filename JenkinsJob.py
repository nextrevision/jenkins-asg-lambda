from __future__ import print_function
import json
import re
import sys
import ConfigParser

import boto3
import credstash
import requests

asg_client = boto3.client('autoscaling')
s3_client = boto3.client('s3')
ec2_resource = boto3.resource('ec2')

def handler(event, context):
    message = json.loads(event['Records'][0]['Sns']['Message'])
    metadata = json.loads(message['NotificationMetadata'])
    print("DEBUG: Event\n%s" % json.dumps(event))
    print("DEBUG: Message\n%s" % json.dumps(message))
    print("DEBUG Metadata\n%s" % json.dumps(metadata))

    instance = ec2_resource.Instance(message['EC2InstanceId'])
    instance_metadata = {
        "id": instance.instance_id,
        "private_hostname": instance.private_dns_name,
        "private_ip": instance.private_ip_address,
        "public_hostname": instance.public_dns_name,
        "public_ip": instance.public_ip_address
    }
    print("DEBUG: Instance Information\n%s" % json.dumps(instance_metadata))

    config_file = 'config.ini'
    if 's3_config_file' in metadata.keys():
        print("Downloading config from s3: %s/%s" % (metadata['s3_bucket'], metadata['s3_config_file']))
        config_file = "/tmp/%s" % metadata['s3_config_file']
        s3_client.download_file(metadata['s3_bucket'], metadata['s3_config_file'], config_file)
    print("Reading settings from %s" % config_file)
    settings = read_config(config_file, instance_metadata)

    if message['LifecycleTransition'] == 'autoscaling:EC2_INSTANCE_LAUNCHING' and settings['call_create_job']:
        print("Calling create job action against %s/job/%s" % (settings['url'], settings['create_job']))
        run_jenkins_job(settings['create_job'], settings['create_job_params'], settings['create_job_token'], settings)
    elif message['LifecycleTransition'] == 'autoscaling:EC2_INSTANCE_TERMINATING' and settings['call_terminate_job']:
        print("Calling terminate job action against %s/job/%s" % (settings['url'], settings['terminate_job']))
        run_jenkins_job(settings['terminate_job'], settings['terminate_job_params'], settings['terminate_job_token'], settings)

    print("Job queued for execution, finishing ASG action")

    response = asg_client.complete_lifecycle_action(
        LifecycleHookName=message['LifecycleHookName'],
        AutoScalingGroupName=message['AutoScalingGroupName'],
        LifecycleActionToken=message['LifecycleActionToken'],
        LifecycleActionResult='CONTINUE',
        InstanceId=instance_metadata['id']
    )
    print("ASG action complete:\n %s" % response)

def run_jenkins_job(job, params, token, settings):
    auth = (settings['username'], settings['api_key'])
    crumb_url = "%s/crumbIssuer/api/xml?xpath=concat(//crumbRequestField,':',//crumb)" % settings['url']
    job_url = "%s/job/%s/buildWithParameters?token=%s&%s&cause=Lambda+ASG+Scale" % (
            settings['url'], job, token, params)

    crumb_response = requests.get(crumb_url, auth=auth)
    crumb = crumb_response.text.split(':')

    job_response = requests.post(job_url, auth=auth, data={}, headers={crumb[0]: crumb[1]})
    if re.match('^2[0-9]{2}$', job_response.status_code) is None:
        print("Reponse from job %s was not 2xx: %s" % (job, job_response.status_code))
        sys.exit(1)


def read_config(config_file, instance_metadata):
    settings = {}
    # get user config
    config = ConfigParser.ConfigParser()
    config.read(config_file)

    # get global config settings
    use_credstash = config.getboolean('DEFAULT', 'use_credstash')
    call_create_job = config.getboolean('DEFAULT', 'call_create_job')
    settings['call_create_job'] = call_create_job
    call_terminate_job = config.getboolean('DEFAULT', 'call_terminate_job')
    settings['call_terminate_job'] = call_terminate_job

    # get jenkins settings
    settings['url'] = config.get('jenkins', 'url')
    settings['verify_ssl'] = config.getboolean('jenkins', 'verify_ssl')
    if call_create_job:
        settings['create_job'] = config.get('jenkins', 'create_job')
        settings['create_job_params'] = config.get('jenkins', 'create_job_params', 0, instance_metadata)
    if call_terminate_job:
        settings['terminate_job'] = config.get('jenkins', 'terminate_job')
        settings['terminate_job_params'] = config.get('jenkins', 'terminate_job_params',0, instance_metadata)

    # get credstash settings
    if use_credstash:
        credstash_table = config.get('credstash', 'table')
        credstash_jenkins_username_key = config.get('credstash', 'jenkins_username_key')
        credstash_jenkins_user_api_key = config.get('credstash', 'jenkins_user_token_key')
        credstash_jenkins_create_job_token_key = config.get('credstash', 'jenkins_create_job_token_key')
        credstash_jenkins_terminate_job_token_key = config.get('credstash', 'jenkins_terminate_job_token_key')
        # populate jenkins settings from credstash
        settings['username'] = credstash.getSecret(credstash_jenkins_username_key, table=credstash_table)
        settings['api_key'] = credstash.getSecret(credstash_jenkins_user_api_key, table=credstash_table)
        if call_create_job:
            settings['create_job_token'] = credstash.getSecret(credstash_jenkins_create_job_token_key, table=credstash_table)
        if call_terminate_job:
            settings['terminate_job_token'] = credstash.getSecret(credstash_jenkins_terminate_job_token_key, table=credstash_table)
    else:
        settings['username'] = config.get('jenkins', 'username')
        settings['api_key'] = config.get('jenkins', 'api_key')
        if call_create_job:
            settings['create_job_token'] = config.get('jenkins', 'create_job_token')
        if call_terminate_job:
            settings['terminate_job_token'] = config.get('jenkins', 'terminate_job_token')

    return settings

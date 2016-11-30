from __future__ import print_function
from os import path

import json
import re
import sys
import ConfigParser

import boto3
import credstash
import requests

# asg lifecycle actions
LAUNCH_STR = 'autoscaling:EC2_INSTANCE_LAUNCHING'
TERMINATE_STR = 'autoscaling:EC2_INSTANCE_TERMINATING'
TEST_STR = 'autoscaling:TEST_NOTIFICATION'

# aws clients and resources
asg_client = boto3.client('autoscaling')
s3_client = boto3.client('s3')
ec2_resource = boto3.resource('ec2')


# main entrypoint for lambda function
def handler(event, context):
    # parse the message and metadata out of the event
    message, metadata = parse_event(event)

    if 'Event' in message.keys() and message['Event'] == TEST_STR:
        print('DEBUG: Ignoring test notification.')
        sys.exit(0)

    print("DEBUG: Received Event\n%s" % json.dumps(event))
    #print("DEBUG: Message\n%s" % json.dumps(message))
    #print("DEBUG: Metadata\n%s" % json.dumps(metadata))

    transition = message['LifecycleTransition']
    instance_id = message['EC2InstanceId']

    # set instance name
    name_prefix = metadata['name_prefix']
    set_instance_name(instance_id, "%s%s" % (name_prefix, instance_id[2:]))

    # build instance metadata to support parameter interpolation
    instance_metadata = get_instance_metadata(instance_id)
    print("DEBUG: Instance Information\n%s" % json.dumps(instance_metadata))

    # determine the config file to use from either a local file or one
    # downloaded from an s3 bucket
    config_file = get_config_file(metadata)
    print("Reading settings from %s" % config_file)

    # load the config file
    settings = read_config(config_file, instance_metadata)

    # run on instance launch and when the user has call_create_job set to true
    if transition == LAUNCH_STR and settings['call_create_job']:
        print("Calling create job %s/job/%s" % (settings['url'],
                                                settings['create_job']))

        run_jenkins_job(settings['create_job'],
                        settings['create_job_params'],
                        settings['create_job_token'],
                        settings)

    # run on instance terminate and when the user has call_terminate_job set
    # to true
    elif transition == TERMINATE_STR and settings['call_terminate_job']:
        print("Calling terminate job %s/job/%s" % (settings['url'],
                                                   settings['terminate_job']))
        run_jenkins_job(settings['terminate_job'],
                        settings['terminate_job_params'],
                        settings['terminate_job_token'],
                        settings)

    # finish the asg lifecycle operation by sending a continue result
    print("Job queued for execution, finishing ASG action")
    response = asg_client.complete_lifecycle_action(
        LifecycleHookName=message['LifecycleHookName'],
        AutoScalingGroupName=message['AutoScalingGroupName'],
        LifecycleActionToken=message['LifecycleActionToken'],
        LifecycleActionResult='CONTINUE',
        InstanceId=instance_metadata['id']
    )
    print("ASG action complete:\n %s" % response)


def set_instance_name(instance_id, name):
    ec2 = boto3.client('ec2')
    tag = {'Key': 'Name', 'Value': name}
    response = ec2.create_tags(Resources=[instance_id], Tags=[tag])
    status_code = response['ResponseMetadata']['HTTPStatusCode']
    if status_code != 200:
        print("Reponse from ec2 name update: %s" % status_code)
        print(json.dumps(response))
        sys.exit(1)


# returns the message and metadata from an event object
def parse_event(event):
    metadata = {}
    message = json.loads(event['Records'][0]['Sns']['Message'])
    if 'NotificationMetadata' in message.keys():
        metadata = json.loads(message['NotificationMetadata'])
    return message, metadata


# builds and returns a metadata object from an ec2 instance id
def get_instance_metadata(instance_id):
    instance = ec2_resource.Instance(instance_id)

    metadata = {
        "id": instance.instance_id,
        "hostname": instance.private_dns_name.split('.')[0],
        "private_hostname": instance.private_dns_name,
        "private_ip": instance.private_ip_address,
        "public_hostname": instance.public_dns_name,
        "public_ip": instance.public_ip_address
    }

    for tag in instance.tags:
        metadata[tag['Key']] = tag['Value']

    return metadata


# when the user has supplied an s3 bucket and config file location in the
# message metadata, download the file to the tmp directory. otherwise a file
# provided with the function, config.ini
def get_config_file(metadata):
    config_file = 'config.ini'
    if 's3_config_file' in metadata.keys():
        config_base_name = path.basename(metadata['s3_config_file'])
        config_file = "/tmp/%s" % config_base_name
        s3_client.download_file(metadata['s3_bucket'],
                                metadata['s3_config_file'],
                                config_file)
    return config_file


# get a csrf token and run the configured jenkins job
def run_jenkins_job(job, params, token, settings):
    job_url = "%s/job/%s/buildWithParameters" % (settings['url'], job)
    job_params = "token=%s&%s&cause=Lambda+ASG+Scale" % (token, params)
    auth = (settings['username'], settings['api_key'])

    if settings['csrf_enabled']:
        # for most jenkins setups, we need a CSRF crumb to subsequently call the
        # jenkins api to trigger a job
        print("DEBUG: Getting CSRF crumb")
        response = requests.get("%s/crumbIssuer/api/json" % settings['url'],
                                auth=auth,
                                timeout=5,
                                verify=settings['verify_ssl'])
        # if we dont get a 2xx response, display the code and dump the response
        if re.match('^2[0-9]{2}$', str(response.status_code)) is None:
            print("Response from crumb was not 2xx: %s" % response.status_code)
            print(response.text)
            sys.exit(1)

        crumb = json.loads(response.text)
        headers = {crumb['crumbRequestField']: crumb['crumb']}

    # call the jenkins job api with the supplied parameters
    response = requests.post("%s?%s" % (job_url, job_params),
                             data={},
                             auth=auth,
                             timeout=5,
                             headers=headers,
                             verify=settings['verify_ssl'])
    # if we dont get a 2xx response, display the code and dump the response
    if re.match('^2[0-9]{2}$', str(response.status_code)) is None:
        print("Response from job %s was not 2xx: %d" % (job, response.status_code))
        print(response.text)
        sys.exit(1)


# parses a config file and returns a settings object
def read_config(config_file, instance_metadata):
    settings = {}
    # get user config
    config = ConfigParser.ConfigParser()
    config.read(config_file)

    # get global config settings
    use_credstash = config.getboolean('DEFAULT', 'use_credstash')
    settings['call_create_job'] = config.getboolean('DEFAULT',
                                                    'call_create_job')
    settings['call_terminate_job'] = config.getboolean('DEFAULT',
                                                       'call_terminate_job')

    # get jenkins settings
    settings['url'] = config.get('jenkins', 'url')
    settings['verify_ssl'] = config.getboolean('jenkins', 'verify_ssl')

    try:
        settings['csrf_enabled'] = config.getboolean('jenkins', 'csrf_enabled')
    except ConfigParser.NoOptionError:
        settings['csrf_enabled'] = False

    if settings['call_create_job']:
        settings['create_job'] = config.get('jenkins', 'create_job')
        settings['create_job_params'] = config.get('jenkins',
                                                   'create_job_params',
                                                   0, instance_metadata)
    if settings['call_terminate_job']:
        settings['terminate_job'] = config.get('jenkins', 'terminate_job')
        settings['terminate_job_params'] = config.get('jenkins',
                                                      'terminate_job_params',
                                                      0, instance_metadata)

    # get credstash settings
    if use_credstash:
        credstash_table = config.get('credstash', 'table')
        settings['username'] = credstash.getSecret(
            config.get('credstash', 'jenkins_username_key'),
            table=credstash_table
        )
        settings['api_key'] = credstash.getSecret(
            config.get('credstash', 'jenkins_user_token_key'),
            table=credstash_table
        )
        if settings['call_create_job']:
            settings['create_job_token'] = credstash.getSecret(
                config.get('credstash', 'jenkins_create_job_token_key'),
                table=credstash_table
            )
        if settings['call_terminate_job']:
            settings['terminate_job_token'] = credstash.getSecret(
                config.get('credstash', 'jenkins_terminate_job_token_key'),
                table=credstash_table
            )
    else:
        settings['username'] = config.get('jenkins', 'username')
        settings['api_key'] = config.get('jenkins', 'api_key')
        if settings['call_create_job']:
            settings['create_job_token'] = config.get('jenkins',
                                                      'create_job_token')
        if settings['call_terminate_job']:
            settings['terminate_job_token'] = config.get('jenkins',
                                                         'terminate_job_token')

    return settings

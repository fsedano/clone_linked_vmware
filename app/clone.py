#!/usr/bin/env python
"""
Based on work from Reuben ur Rahman
"""



import atexit
import requests.packages.urllib3 as urllib3
import ssl
import time

from pyVmomi import vim
from pyVim.connect import SmartConnect, Disconnect

from tools import cli
from tools import tasks
import logging

logging.basicConfig(level=logging.DEBUG,format='%(asctime)s %(message)s')


def get_args():
    parser = cli.build_arg_parser()
    parser.add_argument('-v', '--vm_name',
                        required=True,
                        action='store',
                        help='Name of the new VM')

    parser.add_argument('--template_name',
                        required=True,
                        action='store',
                        help='Name of the template/VM you are cloning from')

    parser.add_argument('--datacenter_name',
                        required=True,
                        action='store',
                        default=None,
                        help='Name of the Datacenter you wish to use.')

    parser.add_argument('--cluster_name',
                        required=True,
                        action='store',
                        default=None,
                        help='Name of the cluster you wish to use')

    parser.add_argument('--host_name',
                        required=False,
                        action='store',
                        default=None,
                        help='Name of the cluster you wish to use')

    parser.add_argument('--vm_ip_address',
                        required=False,
                        action='store',
                        default=None,
                        help='IP of vm')

    parser.add_argument('--vm_ip_mask',
                        required=False,
                        action='store',
                        default=None,
                        help='Mask of vm')

    parser.add_argument('--vm_ip_gateway',
                        required=False,
                        action='store',
                        default=None,
                        help='GW of vm')

    parser.add_argument('--create_template',
                        required=False,
                        action='store_true',
                        help='Is the target a template?')

    parser.add_argument('--template_folder',
                        required=False,
                        default=None,
                        action='store',
                        help='Folder name for template')

    parser.add_argument('--vm_folder',
                        required=False,
                        default=None,
                        action='store',
                        help='Folder name for destination VM')


    args = parser.parse_args()

    cli.prompt_for_password(args)
    return args


def get_obj(content, vimtype, name, folder=None):
    obj = None
    if not folder:
        folder = content.rootFolder
    container = content.viewManager.CreateContainerView(folder, vimtype, True)
    for item in container.view:
        if item.name == name:
            obj = item
            break
    return obj


def _clone_vm(si, template, vm_name, folder, location, customization_spec, create_template):
    logging.info(f"Cloning new VM with name {vm_name}. Power state is {not create_template}")
    clone_spec = vim.vm.CloneSpec(
        powerOn=not create_template, template=create_template, location=location,
        snapshot=template.snapshot.rootSnapshotList[0].snapshot,
        customization=customization_spec)
    task = template.Clone(name=vm_name, folder=folder, spec=clone_spec)
    tasks.wait_for_tasks(si, [task])
    logging.info("Successfully cloned and created the VM '{}' task={}".format(vm_name, task.info.entity.summary))
    return task.info.entity


def _get_relocation_spec(host, resource_pool):
    relospec = vim.vm.RelocateSpec()
    relospec.diskMoveType = 'createNewChildDiskBacking'
    relospec.host = host
    relospec.pool = resource_pool
    return relospec


def _take_template_snapshot(si, vm):
    if len(vm.rootSnapshot) < 1:
        task = vm.CreateSnapshot_Task(name='test_snapshot',
                                      memory=False,
                                      quiesce=False)
        tasks.wait_for_tasks(si, [task])
        print("Successfully taken snapshot of '{}'".format(vm.name))

def _kustomize(vm, vm_name, ip_address_spec):
    customspec = vim.vm.customization.Specification()
    guest_map = []
    for ip_interface_data in ip_address_spec:
        nic = vm.customization.AdapterMapping()
        nic.adapter = vim.vm.customization.IPSettings()
        if ip_interface_data['ip']:
            nic.adapter.ip = vim.vm.customization.FixedIp()
            nic.adapter.ip.ipAddress = ip_interface_data['ip']
            nic.adapter.subnetMask = ip_interface_data['mask']
            nic.adapter.gateway = ip_interface_data['gateway']
        else:
            nic.adapter.ip = vim.vm.customization.DhcpIpGenerator()
        guest_map.append(nic)
    hostname = vm_name.replace(' ', '-')
    hostname = hostname.replace('_', '-')
    hostname = hostname[hostname.rfind('/') + 1:]
    logging.info(f"Creating customization, hostname={hostname}")
    ident = vim.vm.customization.LinuxPrep()
    ident.hostName = vim.vm.customization.FixedName()
    ident.hostName.name = hostname
    
    print(f"Guest map is {guest_map}")
    customspec.nicSettingMap = guest_map
    customspec.identity = ident
    customspec.globalIPSettings = vim.vm.customization.GlobalIPSettings()
    return customspec

def _printinfo(vm):
    logging.debug(f"Summary guest data: {vm.summary.guest}\n")
    logging.debug(f"Guest data: {vm.guest}\n")
#    for nw in vm.network:
#        logging.debug(f"NW data: {nw.summary}\n")
#    logging.info(f" ** IP = {vm.summary.guest.ipAddress}\n")

def find_folder(datacenter, folder_name,root=None):
    if not root:
        root = datacenter.vmFolder
    
    vmFolderList = root.childEntity

    for curItem in vmFolderList:
        if curItem.name == folder_name:
            return curItem
        try:
            target_folder = find_folder(datacenter, folder_name, curItem)
            if target_folder:
                return target_folder
        except:
            pass
    return None

def main():
    args = get_args()

    urllib3.disable_warnings()
    si = None
    context = None
    if hasattr(ssl, 'SSLContext'):
        context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        context.verify_mode = ssl.CERT_NONE
    si = SmartConnect(host=args.host,
                        port=int(args.port),
                        user=args.user,
                        pwd=args.password,
                        sslContext=context)
    atexit.register(Disconnect, si)
    logging.info("Connected to vCenter Server")

    content = si.RetrieveContent()

    datacenter = get_obj(content, [vim.Datacenter], args.datacenter_name)
    if not datacenter:
        raise Exception("Couldn't find the Datacenter with the provided name "
                        "'{}'".format(args.datacenter_name))

    cluster = get_obj(content, [vim.ClusterComputeResource], args.cluster_name,
                      datacenter.hostFolder)

    if not cluster:
        raise Exception("Couldn't find the Cluster with the provided name "
                        "'{}'".format(args.cluster_name))

    host_obj = None
    for host in cluster.host:
        if host.name == args.host_name:
            host_obj = host
            break

    
    template_folder = find_folder(datacenter, args.template_folder)
    if not template_folder:
        template_folder = datacenter.vmFolder

    vm_folder = find_folder(datacenter, args.vm_folder)
    if not vm_folder:
        vm_folder = datacenter.vmFolder


    template = get_obj(content, [vim.VirtualMachine], args.template_name,
                       template_folder)
    if not template:
        raise Exception("Couldn't find the template with the provided name "
                        "'{}'".format(args.template_name))
    else:
        print(f"Template info: {template.network}")
    
    location = _get_relocation_spec(host_obj, cluster.resourcePool)
    _take_template_snapshot(si, template)

    # Generate enough ip address specs to match template.
    # First one is as per configured, others get fake IP addresses
    ip_address_spec = []
    for x in range (len(template.network)):
        if x == 0:
            if args.vm_ip_address:
                ip_data = {
                    'ip': args.vm_ip_address,
                    'mask': args.vm_ip_mask,
                    'gateway': args.vm_ip_gateway
                }
            else:
                ip_data = {
                    'ip': None
                }
        else:
            ip_data = {'ip': f'{10+x}.0.0.1', 'mask':'255.255.255.0', 'gateway': None}
        ip_address_spec.append(ip_data)

    customspec = _kustomize(vim.vm, args.vm_name, ip_address_spec)
    clonedvm = _clone_vm(si, template, args.vm_name, vm_folder, location, customspec, args.create_template)
    _printinfo(clonedvm)
    vm_ip_address = None
    if not args.create_template:
        while True:
            newvm = get_obj(si.RetrieveContent(),[vim.VirtualMachine], args.vm_name)
            _printinfo(newvm)
            if len(newvm.guest.net) > 0:
                vm_ip_address = newvm.summary.guest.ipAddress
                logging.info(f"IP address found: {vm_ip_address}, exit")
                for net in newvm.guest.net:
                    #logging.info(f"IP add={net.ipAddress}")
                    logging.info(f"MAC={net.macAddress}")
                    for ip in net.ipConfig.ipAddress:
                        logging.info(f"IP = {ip.ipAddress} prefixlen={ip.prefixLength}")
                break
            time.sleep(1)


    return vm_ip_address

if __name__ == "__main__":
    main()

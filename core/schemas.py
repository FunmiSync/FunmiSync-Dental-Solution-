from pydantic import BaseModel, EmailStr, ConfigDict, StringConstraints, Field 
from datetime import datetime
from typing import Optional, Literal
from typing import Literal, Annotated, List, Dict,Any
from uuid import UUID


Datestr = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]

class Webhook_requests(BaseModel):
    event_id : str
    contact_id : str 
    commslog: str
    status : str
    Date : str 
    start_time : str
    end_time: str   
    first_name: str 
    last_name : str 
    BirthDate: str 
    Gender: str 
    Notes: Optional[str] = None 
    pop_up: Optional[str] = None 
    calendar_id : str 
    Note : str 
    WirelessPhone:str 
    Email: EmailStr 
    PriProv:str


class webhook_response(BaseModel):
    status: int
    job_id: str
    message: str
    clinic: UUID
    crm_type: str



class patient_model(BaseModel):
    FName: str
    LName: str
    Gender: Optional[str] = None
    Address: Optional[str] = None
    Birthdate: Optional[str] = None
    WirelessPhone: Optional[str] = None
    Email: Optional[EmailStr] = None
    position: Optional[str] = None


class Appointments_create(BaseModel):
    PatNum:int
    Pattern: str 
    AptDateTime : str
    Op: str
    AptStatus: str
    Note: Optional[str] = None

class Appointments_update(  BaseModel):
    Pattern : str 
    AptDateTime : str 
    Op : str 
    AptStatus : str 

class create_commslogs(BaseModel):
    commlogs : str 
    PatNum : int 

class create_pop_ups(BaseModel):
    pop_ups: str 
    PatNum :int 



###########################################  GHL  WORKKS 

class create_contact_ghl(BaseModel):
    firstName: str 
    lastName:str
    email: EmailStr
    phone:str 
    dateOfBirth: Datestr

class create_appointment_ghl(BaseModel):
    calendarId: str 
    locationId: str 
    contactId : str 
    startTime : str 
    endTime : str 
    ignoreFreeSlotValidation : Literal[True]
    assignedUserId : str 
    appointmentStatus : str 
    

class update_appointment_ghl(BaseModel):
    calendarId: str 
    locationId: str  
    startTime : str 
    endTime : str 
    ignoreFreeSlotValidation : Literal[True]
    assignedUserId : str 
    appointmentStatus : str 





################Authentication Schema 
class loginresponse(BaseModel):
    access_token : str
    csrf_token: str

class refreshresponse(BaseModel):
    access_token: str
    csrf_token: str



class loginrequest(BaseModel):
    email : EmailStr
    password: str

class logoutresponse(BaseModel):
    message : str 

###################Googe registration  and login schema 
class google_register_request(BaseModel):
    username:str
    credential:str

class google_login_request(BaseModel):
    credential:str

##################################UserRegistration and clinic registration 
class usercreate(BaseModel):
    username:str 
    email : EmailStr
    password : str
    username:str 

class userout(BaseModel):
    id : UUID 
    email : EmailStr 
    username : str 

    class config:
        orm_mode = True 

class registerdso(BaseModel):
    name : str 

class dsoout(BaseModel):
    id : UUID 
    name : str 
    
    class config:
        orm_mode = True 

class operatorymap(BaseModel):
    calendar_id : str 
    operatories : List[int]

class cliniccreate(BaseModel):
    crm_type: str
    clinic_name : str
    clinic_number : int 
    clinic_timezone : str 
    od_developer_key : str 
    od_customer_key : str 
    crm_api_key : str 
    location_id : str 
    calendar_id : str 
    operatory_calendar_map : Dict[str, List[operatorymap]] = Field ()  

class clinicout(BaseModel):
    id: UUID 
    clinic_name : str 

    class config:
        orm_mode = True


###########################Appointment###############################
class AppointmentRequest(BaseModel):
    date_str : str
    start_str: str
    end_str : str
    status: str
    calendar_id: str
    event_id: str  
    contact_id : str 
    Note : Optional[str] = None 
    pop_up : Optional[str] = None 
    pat_id : UUID 
    commslog : Optional[str] = None 
    pat_Num : int 
    clinic_timezone:  str 

######  Invite Request
class create_dso_invite_request(BaseModel):
    email : EmailStr
    role: Literal["manager", "staff"]

class create_clinic_invite_request(BaseModel):
    email: EmailStr
    role: Literal["manager", "staff"]

class accept_invite_request(BaseModel):
    token: str

class invite_out(BaseModel):
    message: str
    invite_token: str
    expires_at: str

    class config:
        orm_mode = True

#### webhook details 
class webhook_config_out(BaseModel):
    webhook_url: str
    header_name: str
    header_value: str

#Workspace schemas 
class workspace_item(BaseModel):
    scope_type: Literal["dso", "clinic"]
    role: Literal["admin", "manager", "staff"]
    access_source: Literal["owner", "dso_assignment", "clinic_assignment"]
    dso_id: Optional[UUID] = None
    dso_name: Optional[str] = None
    clinic_id: Optional[UUID] = None
    clinic_name: Optional[str] = None



class workspace_ref(BaseModel):
    scope_type: Literal["dso", "clinic"]
    dso_id: Optional[UUID] = None
    clinic_id: Optional[UUID] = None


class my_workspaces_out(BaseModel):
    user_id: UUID
    workspace_count: int
    workspaces: List[workspace_item]
    default_workspace: Optional[workspace_ref] = None
    username: str 



##### For the sync log page 

class sync_log_summary_out(BaseModel):
    synced_today: int 
    in_progress: int
    needs_attention:int
    failed: int

class sync_log_clinic_option_out(BaseModel):
    id: UUID
    name: str

class sync_log_row_out(BaseModel):
    id: UUID
    started_at:datetime
    clinic_id: UUID
    clinic_name:str
    patient_name: Optional[str] = None
    record_label:str
    what_happened:str
    direction:Literal["crm_to_od", "od_to_crm"]
    direction_label:str
    status:Literal["queued", "processing", "retrying", "processed", "failed"]
    status_label:str
    reason:Optional[str] = None
    event_id: Optional[str] = None
    apt_num: Optional[int] = None
    operation: Optional[str] = None
    attempt_count: int 



class sync_log_page_out(BaseModel):
    generated_at: datetime
    visible_count: int
    summary: sync_log_summary_out
    clinics: List[sync_log_clinic_option_out]
    items: List[sync_log_row_out]
    next_cursor: Optional[str] = None


class sync_log_detail_out(sync_log_row_out):
    completed_at: Optional[datetime] = None
    appointment_id: Optional[UUID]
    inbound_event_id: Optional[UUID]
    pat_id: Optional[UUID] = None
    appointment_status: str 
    payload: Optional[Dict[str, Any]] = None



###########  DSO CLINIC PAGE 
class dso_clinic_summary_Out(BaseModel):
    total_clinics: int
    active_clinics: int
    needs_attention: int
    disabled_clinics: int
    synced_today: int

class dso_clinic_actions_out(BaseModel):
    can_view: bool
    can_edit: bool
    can_disable: bool

class dso_clinic_row_out(BaseModel):
    id:UUID
    clinic_name:str
    clinic_number: int
    clinic_timezone:str
    synced_today: int 
    last_sync_at: Optional[datetime] = None
    status:  str 
    needs_attention: bool
    attention_reason: Optional[str] = None
    actions: dso_clinic_actions_out
    disabled_at: Optional[datetime] = None

class dso_clinic_list_out(BaseModel):
    generated_at:datetime
    visible_count: int
    summary: dso_clinic_summary_Out
    items: List[dso_clinic_row_out]


class dso_clinic_disabled_out(BaseModel):
    id:UUID
    clinic_name: str
    status: Literal["disabled"]
    disabled_at: datetime


class team_member_row_out(BaseModel):
    user_id: UUID
    email: str
    role: Literal["admin", "manager", "staff"]
    scope: Literal["dso", "clinic"]
    joined_at: datetime


class team_member_list_out(BaseModel):
    generated_at: datetime
    active_count: int
    items: List[team_member_row_out]





















    
# log.info(
#     "Clinic disabled by DSO user",
#     extra={
#         "dso_id": str(dso_id),
#         "clinic_id": str(clinic_id),
#         "disabled_by": str(disabled_by),
#     },
# )



# log.exception(
#     "Failed to disable clinic",
#     extra={
#         "dso_id": str(dso_id),
#         "clinic_id": str(clinic_id),
#         "disabled_by": str(disabled_by),
#     },
# )


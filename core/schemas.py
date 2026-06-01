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




######Wallet creation 
class toroforge_wallet_create_request(BaseModel):
    username: str = Field(min_length=1, max_length=128)


class toroforge_wallet_create_response(BaseModel):
    wallet_id: UUID
    scope_type: Literal["clinic", "dso"]
    clinic_id: Optional[UUID] = None
    dso_id: Optional[UUID] = None
    external_wallet_address: str
    external_wallet_username: str
    generated_password: str


class toroforge_kyc_submit_request(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    middle_name: Optional[str] = Field(default=None, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    bvn: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=1, max_length=16)
    phone_number: str = Field(min_length=1, max_length=32)
    dob: Datestr
    address: str = Field(min_length=1, max_length=255)


class toroforge_kyc_submit_response(BaseModel):
    wallet_id: UUID
    result: bool
    message: Optional[str] = None
    provider_response: Dict[str, Any]


class toroforge_wallet_kyc_status_response(BaseModel):
    wallet_id: UUID
    verified: bool
    provider: Optional[str] = None


###### Wwallet kyc 
class toroforge_kyc_link_response(BaseModel):
    wallet_id: UUID
    external_wallet_address: str
    kyc_url: str





######## wallet reading##########
class toroforge_wallet_read_item_out(BaseModel):
    wallet_id: UUID
    wallet_type: Literal["dso_treasury", "clinic"]
    wallet_label: str 
    clinic_id: Optional[UUID] = None
    clinic_name: Optional[str] = None
    dso_id: Optional[UUID] = None
    status: str 
    currency: str 
    available_balance_minor: int 
    available_balance: Optional[str] = None
    available_balance_display: Optional[str] = None
    external_wallet_username: Optional[str] = None
    external_wallet_address: Optional[str] = None
    auto_debit_enabled: bool 
    last_balance_sync_at: Optional[datetime] = None 



class toroforge_wallet_ledger_row_out(BaseModel):
    ledger_entry_id: UUID
    wallet_id: UUID
    wallet_label: str
    counterparty_wallet_id: Optional[UUID] = None
    counterparty_wallet_label: Optional[str] = None
    counterparty_clinic_id: Optional[UUID] = None
    counterparty_clinic_name: Optional[str] = None
    event_type: str
    event_label: str
    event_subtitle: Optional[str] = None
    direction: Literal["debit", "credit"]
    status: str
    amount_minor: int
    amount: Optional[str] = None
    amount_display: Optional[str] = None
    balance_after_minor: Optional[int] = None
    balance_after: Optional[str] = None
    balance_after_display: Optional[str] = None
    currency: str
    created_at: datetime
    posted_at: Optional[datetime] = None
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None


class toroforge_billing_subscription_out(BaseModel):
    subscription_id: UUID
    plan_code: str
    status: str
    next_billing_at: Optional[datetime] = None
    amount_minor: int
    amount: Optional[str] = None
    amount_display: Optional[str] = None
    currency: str
    payment_provider: str 


class toroforge_dso_billing_out(BaseModel):
    has_wallet: bool
    next_action: Optional[Literal["create_wallet"]] = None
    message: Optional[str] = None
    generated_at: Optional[datetime] = None
    dso_id: UUID
    treasury_wallet: Optional[toroforge_wallet_read_item_out] = None
    clinic_wallet_count: int = 0
    clinic_wallets: List[toroforge_wallet_read_item_out] = Field(default_factory=list)
    wallet_inflow_this_month_minor: int = 0
    wallet_inflow_this_month: Optional[str] = None
    wallet_inflow_this_month_display: Optional[str] = None
    premium_charges_this_month_minor: int = 0
    premium_charges_this_month: Optional[str] = None
    premium_charges_this_month_display: Optional[str] = None
    failed_payment_count: int = 0
    billing_health_status: Optional[Literal["good", "attention"]] = None
    billing_health_reason: Optional[str] = None
    recent_ledger: List[toroforge_wallet_ledger_row_out] = Field(default_factory=list)
    active_subscription: Optional[toroforge_billing_subscription_out] = None


class toroforge_clinic_billing_out(BaseModel):
    has_wallet:bool
    next_action: Optional[Literal["create_wallet"]] = None
    message: Optional[str] = None
    generated_at: Optional[datetime] = None
    clinic_id: UUID
    dso_id: Optional[UUID] = None
    clinic_wallet: Optional[toroforge_wallet_read_item_out] = None
    parent_wallet_label: Optional[str] = None
    wallet_inflow_this_month_minor: int = 0
    wallet_inflow_this_month: Optional[str] = None
    wallet_inflow_this_month_display: Optional[str] = None
    premium_charges_this_month_minor: int = 0
    premium_charges_this_month: Optional[str] = None
    premium_charges_this_month_display: Optional[str] = None
    failed_payment_count: int = 0
    billing_health_status: Optional[Literal["good", "attention"]] = None
    billing_health_reason: Optional[str] = None
    recent_ledger: List[toroforge_wallet_ledger_row_out] = Field(default_factory=list)
    active_subscription: Optional[toroforge_billing_subscription_out] = None












